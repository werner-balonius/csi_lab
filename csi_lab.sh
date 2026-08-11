#!/bin/bash
# csi_lab.sh — 1T2R CSI + 深度相机联合采集编排（在 Mac 上运行）
#
#   csi_lab.sh <trial> [秒数]        单次采集
#   csi_lab.sh batch <批量文件>       按文件批量采集
#   csi_lab.sh check                 只做预检，不采集
#   csi_lab.sh restore               恢复三台节点的 Wi-Fi
#
# 沿革
# ----
# 合并两套前身，各取所长：
#   来自 Mac 端 run_experiment.sh —— Mac 主控、带模式标签的结果目录、
#       回收后大小校验、NO_RESTORE 开关。
#   来自 node2 端 run_1tx2rx.sh   —— 先确认两个接收端的 PicoScenes 确实在跑
#       才发包（不是 sleep 猜）、rx 时长 = tx + 余量、对齐后 NPZ 完整性校验。
#   新增 —— 深度相机、多 trial 批量、按 PHY 规范化子载波。
#
# 关键约束
# --------
#   * macOS 自带 bash 3.2，不支持 declare -A / mapfile；本文件只用索引数组。
#   * date +%s%N 在 BSD date 上无效，纳秒时间戳一律由节点端生成。
#   * 发射端时长必须显式传给 agent。旧版 run_experiment.sh 调用 `tx` 不带参数，
#     而新版 agent 缺省 30 秒 —— 超过 30 秒的实验会静默只发 30 秒，
#     且 stop 时进程已退出、返回 0，全程无报错。这是本次合并要消灭的坑。

set -uo pipefail

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
CONFIG_FILE="${CSI_LAB_CONFIG:-$SCRIPT_DIR/csi_lab.conf}"
[ -f "$CONFIG_FILE" ] && . "$CONFIG_FILE"

TX_NODE="${TX_NODE:-node2}"
RX_A="${RX_A:-node1}"
RX_B="${RX_B:-node3}"
CAM_HOST="${CAM_HOST:-}"                 # 留空则不采相机
CSI_AGENT="${CSI_AGENT:-csi_agent.sh}"   # 必须带 CSI_ 前缀：
                                         # 通用名 AGENT 会被外部环境变量污染
RESULTS="${RESULTS:-$HOME/csi_results}"
RX_MARGIN="${RX_MARGIN:-15}"             # 接收端比发射端多跑的秒数
TX_MARGIN="${TX_MARGIN:-5}"              # 覆盖 PicoScenes 启动开销
READY_TIMEOUT="${READY_TIMEOUT:-60}"
CAM_READY_TIMEOUT="${CAM_READY_TIMEOUT:-45}"
NO_RESTORE="${NO_RESTORE:-0}"
BATCH_GAP="${BATCH_GAP:-10}"             # 批量采集两组之间的准备时间
PYTHON_BIN="${PYTHON_BIN:-$HOME/csienv/bin/python}"
ALIGN_PY="${ALIGN_PY:-$SCRIPT_DIR/csi_align.py}"
VALIDATE_PY="${VALIDATE_PY:-$SCRIPT_DIR/validate_trial.py}"

CSI_PHY="${CSI_PHY:-1}"
CSI_CHANNEL="${CSI_CHANNEL:-2412}"
CSI_BANDWIDTH="${CSI_BANDWIDTH:-20}"
CSI_PRESET="${CSI_PRESET:-TX_CBW_20_HT}"
CSI_TRAFFIC_MODE="${CSI_TRAFFIC_MODE:-broadcast}"
CSI_TARGET_MAC="${CSI_TARGET_MAC:-}"
CSI_DELAY_US="${CSI_DELAY_US:-5000}"
CSI_ALLOW_CAMERA_WARNINGS="${CSI_ALLOW_CAMERA_WARNINGS:-0}"
if [ -z "${CSI_EXPECTED_PACKET_FORMAT:-}" ]; then
    case "$CSI_PRESET" in
        *_HT|*_HT_LDPC) CSI_EXPECTED_PACKET_FORMAT=HT ;;
        *_VHT|*_VHT_LDPC) CSI_EXPECTED_PACKET_FORMAT=VHT ;;
        *_HESU|*_HESU_LDPC) CSI_EXPECTED_PACKET_FORMAT=HESU ;;
        *) CSI_EXPECTED_PACKET_FORMAT=UNKNOWN ;;
    esac
fi
if [ -z "${CSI_EXPECTED_SUBCARRIERS:-}" ]; then
    case "$CSI_PRESET" in
        TX_CBW_20_HT|TX_CBW_20_HT_LDPC|TX_CBW_20_VHT|TX_CBW_20_VHT_LDPC) CSI_EXPECTED_SUBCARRIERS=56 ;;
        TX_CBW_80_VHT|TX_CBW_80_VHT_LDPC) CSI_EXPECTED_SUBCARRIERS=234 ;;
        TX_CBW_20_HESU|TX_CBW_20_HESU_LDPC) CSI_EXPECTED_SUBCARRIERS=234 ;;
        *) CSI_EXPECTED_SUBCARRIERS=0 ;;
    esac
fi

SSH_OPTS="-o BatchMode=yes -o ConnectTimeout=8 -o ServerAliveInterval=5 -o ServerAliveCountMax=4"

RX_A_PID=""
RX_B_PID=""
CAM_PID=""
TX_RUNNING=0
TRIAL_OK=0

c_red()  { printf '\033[31m%s\033[0m\n' "$*"; }
c_grn()  { printf '\033[32m%s\033[0m\n' "$*"; }
c_ylw()  { printf '\033[33m%s\033[0m\n' "$*"; }
log()    { printf '[lab] %s\n' "$*"; }
warn()   { c_ylw "[lab] 警告: $*"; }
die()    { c_red  "[lab] 错误: $*" >&2; exit 1; }
trial_fail() { c_red "[lab] trial 失败: $*" >&2; return 1; }

agent() {
    local host="$1"; shift
    local remote="env" name value quoted arg
    for name in CSI_PHY CSI_CHANNEL CSI_BANDWIDTH CSI_PRESET CSI_TRAFFIC_MODE \
                CSI_TARGET_MAC CSI_DELAY_US CSI_ALLOW_CAMERA_WARNINGS; do
        value="${!name}"
        printf -v quoted ' %q' "$name=$value"
        remote="$remote$quoted"
    done
    remote="$remote bash \"\$HOME/$CSI_AGENT\""
    for arg in "$@"; do
        printf -v quoted ' %q' "$arg"
        remote="$remote$quoted"
    done
    # shellcheck disable=SC2086
    ssh $SSH_OPTS "$host" "$remote"
}

usage() {
    cat <<'EOF'
用法（在 Mac 上运行）:
  ./csi_lab.sh <trial> [秒数]       单次采集，默认 30 秒
  ./csi_lab.sh batch <批量文件>      批量采集
  ./csi_lab.sh check                只做预检
  ./csi_lab.sh restore              恢复三台 Wi-Fi

环境变量:
  CAM_HOST=node1     指定相机宿主；留空则不采相机
  NO_RESTORE=1       采集后不恢复 Wi-Fi（批量采集内部自动使用）
  TX_NODE / RX_A / RX_B    默认 node2 / node1 / node3

批量文件格式（# 开头为注释）:
  # trial名   秒数   重复次数
  empty        30     3
  walk_link1   30     3
  walk_link2   30     3
EOF
}

# ------------------------------------------------------------------ 清理
stop_all() {
    [ "$TX_RUNNING" = "1" ] && agent "$TX_NODE" stop tx >/dev/null 2>&1
    agent "$RX_A" stop rx >/dev/null 2>&1
    agent "$RX_B" stop rx >/dev/null 2>&1
    [ -n "$CAM_HOST" ] && agent "$CAM_HOST" stop cam >/dev/null 2>&1
    TX_RUNNING=0
    return 0
}

restore_all() {
    local n
    for n in "$RX_A" "$TX_NODE" "$RX_B"; do
        agent "$n" restore >/dev/null 2>&1 \
            || warn "$n 恢复失败，可手动执行: ssh $n 'bash ~/$CSI_AGENT restore'"
    done
    log "三台 Wi-Fi 已恢复"
}

on_exit() {
    local status=$?
    trap - EXIT INT TERM
    if [ "$TRIAL_OK" != "1" ]; then
        c_red "[lab] 流程异常，正在停止采集并尽量保住已有数据..."
        stop_all
        [ "$NO_RESTORE" = "1" ] || restore_all
    fi
    exit "$status"
}

# ------------------------------------------------------------------ 预检
preflight() {
    log "预检：三台节点 + Mac 环境"
    [ -f "$ALIGN_PY" ] || die "找不到对齐脚本 $ALIGN_PY"
    [ -f "$VALIDATE_PY" ] || die "找不到验收脚本 $VALIDATE_PY"
    case "$CSI_EXPECTED_SUBCARRIERS" in
        ''|*[!0-9]*|0) die "必须为 $CSI_PRESET 设置正整数 CSI_EXPECTED_SUBCARRIERS" ;;
    esac
    [ "$CSI_EXPECTED_PACKET_FORMAT" != "UNKNOWN" ] \
        || die "无法从 $CSI_PRESET 推导预期 packet_format"
    [ -x "$PYTHON_BIN" ] || die "找不到 Python: ${PYTHON_BIN}（建议 python3 -m venv ~/csienv && ~/csienv/bin/pip install numpy）"
    "$PYTHON_BIN" -c "import numpy" 2>/dev/null || die "Mac 端 Python 缺少 numpy"

    local n
    for n in "$RX_A" "$RX_B" "$TX_NODE" ${CAM_HOST:+$CAM_HOST}; do
        ssh $SSH_OPTS "$n" "test -f \"\$HOME/$CSI_AGENT\"" \
            || die "$n 上没有 ~/${CSI_AGENT}，先分发脚本"
    done

    agent "$RX_A" check-rx  | sed 's/^/     /' || die "$RX_A 自检失败"
    agent "$RX_B" check-rx  | sed 's/^/     /' || die "$RX_B 自检失败"
    agent "$TX_NODE" check  | sed 's/^/     /' || die "$TX_NODE 自检失败"
    if [ -n "$CAM_HOST" ]; then
        agent "$CAM_HOST" check-cam | sed 's/^/     /' || die "$CAM_HOST 相机自检失败"
    else
        warn "未指定 CAM_HOST，本次不采集 RGB-D 参考数据（当前流程本身不生成姿态标签）"
    fi
    c_grn "[lab] 预检通过"
}

# 等待远端 logger 真正进入采集状态。
# 不能只 sleep：PicoScenes 启动失败时进程会立刻退出，
# 而 array_prepare 静默失败时进程活着但收不到包。这里两个条件都要满足。
wait_logger() {
    local host="$1" logfile="$2" pid="$3" i
    for ((i=0; i<READY_TIMEOUT; i++)); do
        if ! kill -0 "$pid" 2>/dev/null; then
            tail -n 20 "$logfile" >&2
            return 1
        fi
        if grep -q "开始采集" "$logfile" 2>/dev/null \
           && ssh $SSH_OPTS "$host" "pgrep -x PicoScenes >/dev/null" 2>/dev/null; then
            return 0
        fi
        sleep 1
    done
    tail -n 20 "$logfile" >&2
    return 1
}

wait_camera() {
    local logfile="$1" pid="$2" i
    for ((i=0; i<CAM_READY_TIMEOUT; i++)); do
        if ! kill -0 "$pid" 2>/dev/null; then
            tail -n 20 "$logfile" >&2
            return 1
        fi
        grep -q "CAM_READY=1" "$logfile" 2>/dev/null && return 0
        sleep 1
    done
    tail -n 20 "$logfile" >&2
    return 1
}

kv() { awk -F= -v k="$1" '$1==k{v=substr($0,index($0,"=")+1)} END{print v}' "$2"; }

# ------------------------------------------------------------------ 单次采集
run_trial() {
    local trial="$1" dur="$2"
    local rx_dur=$((dur + RX_MARGIN))
    local mode; mode=$([ -n "$CAM_HOST" ] && echo "1t2r_cam" || echo "1t2r")
    local stamp; stamp=$(date +%Y%m%d_%H%M%S)
    local dest="$RESULTS/${stamp}_${mode}_${trial}"

    mkdir -p "$RESULTS" || { trial_fail "无法创建 $RESULTS"; return 1; }
    local suffix=0 base_dest="$dest"
    while ! mkdir "$dest" 2>/dev/null; do
        suffix=$((suffix + 1))
        dest="${base_dest}_$suffix"
        [ "$suffix" -lt 100 ] || { trial_fail "无法创建唯一实验目录"; return 1; }
    done
    LAST_TRIAL_DIR="$dest"
    TRIAL_OK=0
    log "=========================================================="
    log "trial=$trial  发包 ${dur}s  接收 ${rx_dur}s  ->  $dest"

    local rxa_log="$dest/_${RX_A}_control.log"
    local rxb_log="$dest/_${RX_B}_control.log"
    local cam_log="$dest/_cam_control.log"

    # 先收后发：接收端和相机都确认就绪，才开始发包
    agent "$RX_A" rx "$trial" "$rx_dur" >"$rxa_log" 2>&1 &
    RX_A_PID=$!
    agent "$RX_B" rx "$trial" "$rx_dur" >"$rxb_log" 2>&1 &
    RX_B_PID=$!
    if [ -n "$CAM_HOST" ]; then
        agent "$CAM_HOST" cam "$trial" "$rx_dur" >"$cam_log" 2>&1 &
        CAM_PID=$!
    fi

    wait_logger "$RX_A" "$rxa_log" "$RX_A_PID" || { trial_fail "$RX_A logger 未在 ${READY_TIMEOUT}s 内就绪"; return 1; }
    log "$RX_A logger 就绪"
    wait_logger "$RX_B" "$rxb_log" "$RX_B_PID" || { trial_fail "$RX_B logger 未在 ${READY_TIMEOUT}s 内就绪"; return 1; }
    log "$RX_B logger 就绪"
    if [ -n "$CAM_HOST" ]; then
        wait_camera "$cam_log" "$CAM_PID" || { trial_fail "相机未在 ${CAM_READY_TIMEOUT}s 内就绪"; return 1; }
        log "$CAM_HOST 相机就绪"
    fi

    # 时长必须显式传递，缺省会静默变成 30 秒
    c_grn "[lab] 全部就绪，开始发包 ${dur}s"
    local tx_log="$dest/_tx_start.log"
    agent "$TX_NODE" tx "$dur" >"$tx_log" 2>&1 || { cat "$tx_log" >&2; trial_fail "发射端启动失败"; return 1; }
    TX_RUNNING=1
    sed 's/^/     /' "$tx_log"

    local tx_repeat; tx_repeat=$(kv TX_REPEAT "$tx_log")
    local tx_launch; tx_launch=$(kv TX_LAUNCH_SYSTEM_NS "$tx_log")
    [ -n "$tx_repeat" ] || { trial_fail "发射端日志缺少 TX_REPEAT"; return 1; }

    sleep $((dur + TX_MARGIN))
    agent "$TX_NODE" stop tx >"$dest/_tx_stop.log" 2>&1 || warn "发射端停止异常"
    TX_RUNNING=0
    log "发包结束，等待接收端与相机收尾落盘..."

    wait "$RX_A_PID" || { tail -n 25 "$rxa_log" >&2; trial_fail "$RX_A 采集失败"; return 1; }
    wait "$RX_B_PID" || { tail -n 25 "$rxb_log" >&2; trial_fail "$RX_B 采集失败"; return 1; }
    if [ -n "$CAM_HOST" ]; then
        wait "$CAM_PID" || { tail -n 25 "$cam_log" >&2; trial_fail "相机采集失败"; return 1; }
    fi

    # ---------------------------------------------------------- 回收
    local a_csi b_csi a_npz b_npz
    a_csi=$(kv CSIFILE "$rxa_log");  b_csi=$(kv CSIFILE "$rxb_log")
    a_npz=$(kv TARGETNPZ "$rxa_log"); b_npz=$(kv TARGETNPZ "$rxb_log")
    [ -n "$a_csi" ] || { trial_fail "$RX_A 日志缺少 CSIFILE"; return 1; }
    [ -n "$b_csi" ] || { trial_fail "$RX_B 日志缺少 CSIFILE"; return 1; }
    [ -n "$a_npz" ] || { trial_fail "$RX_A 未生成 target_csi.npz"; return 1; }
    [ -n "$b_npz" ] || { trial_fail "$RX_B 未生成 target_csi.npz"; return 1; }

    log "回收数据..."
    fetch "$RX_A" "$a_csi" "$dest/node1.csi" 10240 || return 1
    fetch "$RX_A" "$a_npz" "$dest/node1_target_csi.npz" 10240 || return 1
    fetch "$RX_B" "$b_csi" "$dest/node3.csi" 10240 || return 1
    fetch "$RX_B" "$b_npz" "$dest/node3_target_csi.npz" 10240 || return 1
    fetch "$RX_A" "$(dirname "$a_csi")/metadata.txt" "$dest/node1_metadata.txt" || return 1
    fetch "$RX_B" "$(dirname "$b_csi")/metadata.txt" "$dest/node3_metadata.txt" || return 1

    local cam_meta_local=""
    if [ -n "$CAM_HOST" ]; then
        local cam_depth cam_color cam_meta
        cam_depth=$(kv CAM_DEPTH_FILE "$cam_log")
        cam_color=$(kv CAM_COLOR_FILE "$cam_log")
        cam_meta=$(kv CAM_META_FILE "$cam_log")
        [ -n "$cam_depth" ] || { trial_fail "相机日志缺少 CAM_DEPTH_FILE"; return 1; }
        [ -n "$cam_color" ] || { trial_fail "相机日志缺少 CAM_COLOR_FILE"; return 1; }
        [ -n "$cam_meta" ] || { trial_fail "相机日志缺少 CAM_META_FILE"; return 1; }
        log "回收相机数据（深度约 14 MB/s，百兆网需要一点时间）..."
        fetch "$CAM_HOST" "$cam_depth" "$dest/${cam_depth##*/}" || return 1
        fetch "$CAM_HOST" "$cam_color" "$dest/${cam_color##*/}" || return 1
        fetch "$CAM_HOST" "$cam_meta" "$dest/${cam_meta##*/}" || return 1
        cam_meta_local="$dest/${cam_meta##*/}"
    fi

    # ---------------------------------------------------------- 对齐
    log "包级对齐 + 子载波规范化 + 时钟漂移校正${CAM_HOST:+ + 相机配对}..."
    local align_log="$dest/alignment.log"
    if [ -n "$cam_meta_local" ]; then
        "$PYTHON_BIN" "$ALIGN_PY" "$dest/node1.csi" "$dest/node3.csi" \
            --node1-target-npz "$dest/node1_target_csi.npz" \
            --node3-target-npz "$dest/node3_target_csi.npz" \
            --output-dir "$dest/aligned" --export-npz \
            --cam-meta "$cam_meta_local" >"$align_log" 2>&1 \
            || { cat "$align_log" >&2; trial_fail "对齐失败"; return 1; }
    else
        "$PYTHON_BIN" "$ALIGN_PY" "$dest/node1.csi" "$dest/node3.csi" \
            --node1-target-npz "$dest/node1_target_csi.npz" \
            --node3-target-npz "$dest/node3_target_csi.npz" \
            --output-dir "$dest/aligned" --export-npz >"$align_log" 2>&1 \
            || { cat "$align_log" >&2; trial_fail "对齐失败"; return 1; }
    fi
    sed 's/^/     /' "$align_log"

    local npz="$dest/aligned/aligned_csi.npz"
    [ -s "$npz" ] || { trial_fail "对齐未生成 aligned_csi.npz"; return 1; }
    if [ -n "$cam_meta_local" ]; then
        "$PYTHON_BIN" "$VALIDATE_PY" "$npz" \
            --expected-subcarriers "$CSI_EXPECTED_SUBCARRIERS" \
            --expected-packet-format "$CSI_EXPECTED_PACKET_FORMAT" \
            --expected-bandwidth-mhz "$CSI_BANDWIDTH" \
            --require-camera --camera-meta "$cam_meta_local" \
            --json-output "$dest/aligned/validation_report.json" \
            || { trial_fail "严格完整性检查失败"; return 1; }
    else
        "$PYTHON_BIN" "$VALIDATE_PY" "$npz" \
            --expected-subcarriers "$CSI_EXPECTED_SUBCARRIERS" \
            --expected-packet-format "$CSI_EXPECTED_PACKET_FORMAT" \
            --expected-bandwidth-mhz "$CSI_BANDWIDTH" \
            --json-output "$dest/aligned/validation_report.json" \
            || { trial_fail "严格完整性检查失败"; return 1; }
    fi

    cat > "$dest/experiment.txt" <<EOF
trial: $trial
mode: $mode
tx_node: $TX_NODE
rx_nodes: $RX_A $RX_B
cam_host: ${CAM_HOST:-无}
tx_duration_s: $dur
rx_duration_s: $rx_dur
tx_expected_packets: $tx_repeat
tx_launch_system_ns: $tx_launch
started_at: $stamp
phy_index: $CSI_PHY
channel_mhz: $CSI_CHANNEL
bandwidth_mhz: $CSI_BANDWIDTH
preset: $CSI_PRESET
csi_segment: CSI
traffic_mode: $CSI_TRAFFIC_MODE
target_mac: ${CSI_TARGET_MAC:-由模式决定}
expected_data_subcarriers: $CSI_EXPECTED_SUBCARRIERS
expected_packet_format: $CSI_EXPECTED_PACKET_FORMAT
EOF

    TRIAL_OK=1
    c_grn "[lab] trial 完成 -> $dest"
    du -sh "$dest" | awk '{print "     体积 " $1}'
}

fetch() {
    local host="$1" remote="$2" local_path="$3" min_size="${4:-1}"
    local quoted remote_size size
    printf -v quoted '%q' "$remote"
    remote_size=$(ssh $SSH_OPTS "$host" "stat -c %s $quoted" 2>/dev/null) \
        || { trial_fail "$host 无法读取 $remote 的远端大小"; return 1; }
    scp -q $SSH_OPTS "$host:$remote" "$local_path" \
        || { trial_fail "$host 回收 $remote 失败"; return 1; }
    size=$(stat -f%z "$local_path" 2>/dev/null || stat -c%s "$local_path" 2>/dev/null || echo 0)
    [ "$size" -ge "$min_size" ] \
        || { trial_fail "$local_path 仅 $size 字节，判定无效"; return 1; }
    [ "$size" = "$remote_size" ] \
        || { trial_fail "$local_path 本地大小 $size 与远端大小 $remote_size 不一致"; return 1; }
}

# ------------------------------------------------------------------ 批量
run_batch() {
    local file="$1"
    [ -f "$file" ] || die "找不到批量文件 $file"

    local names=() durs=() reps=() total=0 line trial dur rep
    while IFS= read -r line || [ -n "$line" ]; do
        case "$line" in ''|\#*) continue ;; esac
        trial=$(echo "$line" | awk '{print $1}')
        dur=$(echo   "$line" | awk '{print ($2==""?30:$2)}')
        rep=$(echo   "$line" | awk '{print ($3==""?1:$3)}')
        case "$trial" in
            ''|*[!A-Za-z0-9._-]*) die "批量 trial 名非法: $trial" ;;
        esac
        case "$dur" in ''|*[!0-9]*|0) die "批量时长必须是正整数: $dur" ;; esac
        case "$rep" in ''|*[!0-9]*|0) die "批量重复次数必须是正整数: $rep" ;; esac
        names+=("$trial"); durs+=("$dur"); reps+=("$rep")
        total=$((total + rep))
    done < "$file"
    [ "${#names[@]}" -gt 0 ] || die "批量文件里没有有效条目"

    log "批量采集：${#names[@]} 种场景，共 $total 组"
    preflight

    # 批量过程中始终不恢复 Wi-Fi，否则每组都要等 NM 重连，且反复切换容易出错
    local saved_restore="$NO_RESTORE"
    NO_RESTORE=1

    mkdir -p "$RESULTS" || die "无法创建 $RESULTS"
    local batch_summary="$RESULTS/batch_$(date +%Y%m%d_%H%M%S)_summary.tsv"
    printf 'trial\tstatus\tresult_directory\n' > "$batch_summary"
    local done_count=0 failed_count=0 i r idx
    for ((i=0; i<${#names[@]}; i++)); do
        for ((r=1; r<=${reps[$i]}; r++)); do
            done_count=$((done_count + 1))
            idx="${names[$i]}_$(printf '%02d' "$r")"
            c_ylw "[lab] ---- 第 $done_count/$total 组：$idx ----"
            if [ "$done_count" -gt 1 ]; then
                log "准备时间 ${BATCH_GAP}s（就位后可按回车提前开始）"
                read -r -t "$BATCH_GAP" _ 2>/dev/null || true
            fi
            if ! run_trial "$idx" "${durs[$i]}"; then
                warn "第 $done_count 组失败，继续下一组"
                printf '%s\tFAILED\t%s\n' "$idx" "${LAST_TRIAL_DIR:-}" >> "$batch_summary"
                failed_count=$((failed_count + 1))
                stop_all
            else
                printf '%s\tOK\t%s\n' "$idx" "${LAST_TRIAL_DIR:-}" >> "$batch_summary"
            fi
            TRIAL_OK=0  # batch remains active until final restore/summary
        done
    done

    NO_RESTORE="$saved_restore"
    TRIAL_OK=1
    c_grn "[lab] 批量采集结束，共 $total 组，失败 $failed_count 组"
    log "批量结果表: $batch_summary"
    [ "$NO_RESTORE" = "1" ] || restore_all
    [ "$failed_count" -eq 0 ] || return 1
}

# ------------------------------------------------------------------ main
trap on_exit EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

ACTION="${1:-}"
case "$ACTION" in
    ''|-h|--help|help) usage; TRIAL_OK=1; exit 0 ;;
    check)   preflight; TRIAL_OK=1 ;;
    restore) restore_all; TRIAL_OK=1 ;;
    batch)
        [ -n "${2:-}" ] || die "用法: csi_lab.sh batch <批量文件>"
        run_batch "$2"
        ;;
    *)
        TRIAL="$ACTION"
        DUR="${2:-30}"
        case "$TRIAL" in
            *[!A-Za-z0-9._-]*) die "trial 名只能含字母、数字、点、下划线、连字符" ;;
        esac
        case "$DUR" in
            ''|*[!0-9]*) die "秒数必须是正整数" ;;
        esac
        [ "$DUR" -gt 0 ] || die "秒数必须大于 0"
        preflight
        run_trial "$TRIAL" "$DUR" || exit $?
        [ "$NO_RESTORE" = "1" ] && log "NO_RESTORE=1，保持采集状态（记得最后执行 csi_lab.sh restore）" || restore_all
        ;;
esac
