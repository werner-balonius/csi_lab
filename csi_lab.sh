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
#   新增 —— 深度相机、多 trial 批量、导频剔除。
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
CSI_CHANNEL="${CSI_CHANNEL:-2412 20}"    # PicoScenes 频率 MHz + 带宽 MHz
RESULTS="${RESULTS:-$HOME/csi_results}"
RX_MARGIN="${RX_MARGIN:-15}"             # 接收端比发射端多跑的秒数
TX_MARGIN="${TX_MARGIN:-5}"              # 覆盖 PicoScenes 启动开销
READY_TIMEOUT="${READY_TIMEOUT:-60}"
CAM_READY_TIMEOUT="${CAM_READY_TIMEOUT:-45}"
NO_RESTORE="${NO_RESTORE:-0}"
BATCH_GAP="${BATCH_GAP:-10}"             # 批量采集两组之间的准备时间
LOCAL_VOICE_PROTOCOL="${LOCAL_VOICE_PROTOCOL:-}" # Mac 本地动作提示
LOCAL_VOICE_ACTION="${LOCAL_VOICE_ACTION:-none}"
LAYOUT_ID="${LAYOUT_ID:-}"
NODE1_XYZ_M="${NODE1_XYZ_M:-}"
NODE2_XYZ_M="${NODE2_XYZ_M:-}"
NODE3_XYZ_M="${NODE3_XYZ_M:-}"
CAMERA_XYZ_M="${CAMERA_XYZ_M:-}"
CAMERA_ORIENTATION="${CAMERA_ORIENTATION:-}"
LAYOUT_REFERENCE="${LAYOUT_REFERENCE:-}"
CHAIR_CENTER_XYZ_M="${CHAIR_CENTER_XYZ_M:-}"
CHAIR_ORIENTATION="${CHAIR_ORIENTATION:-}"
CHAIR_SEAT_HEIGHT_M="${CHAIR_SEAT_HEIGHT_M:-}"
CHAIR_DESCRIPTION="${CHAIR_DESCRIPTION:-}"
CHAIR_STABILITY="${CHAIR_STABILITY:-}"
PYTHON_BIN="${PYTHON_BIN:-$HOME/csienv/bin/python}"
ALIGN_PY="${ALIGN_PY:-$SCRIPT_DIR/csi_align.py}"

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

agent() {
    local host="$1"; shift
    # shellcheck disable=SC2086
    ssh $SSH_OPTS "$host" "CSI_CHANNEL=\"$CSI_CHANNEL\" bash \"\$HOME/$CSI_AGENT\" $*"
}

usage() {
    cat <<'EOF'
用法（在 Mac 上运行）:
  ./csi_lab.sh <trial> [秒数]       单次采集，默认 30 秒
  ./csi_lab.sh batch <批量文件>      批量采集
  ./csi_lab.sh check                只做预检
  ./csi_lab.sh restore              恢复三台 Wi-Fi

环境变量:
  CAM_HOST=node3     指定相机宿主（当前现场为 node3）；留空则不采相机
  NO_RESTORE=1       采集后不恢复 Wi-Fi（批量采集内部自动使用）
  TX_NODE / RX_A / RX_B    默认 node2 / node1 / node3
  CSI_CHANNEL="2462 20"    指定 PicoScenes 频率 MHz 与带宽 MHz
  LOCAL_VOICE_PROTOCOL=action_5_20_10
                    Mac 本地提示：空场 5s、动作/离场约 20s、末尾空场
  LOCAL_VOICE_ACTION=walk_link2|arm_wave|leg_lift|sit_to_stand|auto
                    auto 根据 distance-boundary trial 名选择静止、运动或金属板提示

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
        warn "未指定 CAM_HOST，本次不采集深度相机（没有姿态真值）"
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
    local action_label="$LOCAL_VOICE_ACTION"
    if [ "$action_label" = "auto" ]; then
        case "$trial" in
            *_fov_lcr_*)   action_label="multirx_fov" ;;
            *_static_*)    action_label="distance_static" ;;
            *_motion_*)    action_label="distance_motion" ;;
            *_plate_mid_*) action_label="distance_plate_mid" ;;
            *_plate_rx_*)  action_label="distance_plate_rx" ;;
            *)             action_label="none" ;;
        esac
    fi
    local mode; mode=$([ -n "$CAM_HOST" ] && echo "1t2r_cam" || echo "1t2r")
    local stamp; stamp=$(date +%Y%m%d_%H%M%S)
    local dest="$RESULTS/${stamp}_${mode}_${trial}"

    mkdir -p "$dest" || die "无法创建 $dest"
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

    wait_logger "$RX_A" "$rxa_log" "$RX_A_PID" || die "$RX_A logger 未在 ${READY_TIMEOUT}s 内就绪"
    log "$RX_A logger 就绪"
    wait_logger "$RX_B" "$rxb_log" "$RX_B_PID" || die "$RX_B logger 未在 ${READY_TIMEOUT}s 内就绪"
    log "$RX_B logger 就绪"
    if [ -n "$CAM_HOST" ]; then
        wait_camera "$cam_log" "$CAM_PID" || die "相机未在 ${CAM_READY_TIMEOUT}s 内就绪"
        log "$CAM_HOST 相机就绪"
    fi

    # 时长必须显式传递，缺省会静默变成 30 秒
    c_grn "[lab] 全部就绪，开始发包 ${dur}s"
    local tx_log="$dest/_tx_start.log"
    agent "$TX_NODE" tx "$dur" >"$tx_log" 2>&1 || { cat "$tx_log" >&2; die "发射端启动失败"; }
    TX_RUNNING=1
    sed 's/^/     /' "$tx_log"

    # agent 返回点实测约比第一个接收包早 0.2 秒。各提示用独立定时器
    # 锚定，避免 say 自身播报时长累积造成动作窗漂移。约第 22 秒提示
    # 最后一次穿越并离场，给参与者约 3 秒退出，确保末尾 5 秒为空场。
    if [ "$action_label" != "none" ] \
       && { [ "$LOCAL_VOICE_PROTOCOL" = "action_5_20_10" ] \
            || [ "$LOCAL_VOICE_PROTOCOL" = "walk_link2_5_20_5" ]; }; then
        if command -v say >/dev/null 2>&1; then
            local action_prompt end_prompt pos_label
            end_prompt='完成最后一次动作，并立即离开实验区域。'
            # 距离实验的站位由 trial 名给出，必须在语音里念出来：
            # 批次顺序由固定随机种子打乱，参与者在场内无法自行判断该站哪个标记。
            case "$trial" in
                *_off00_*) pos_label='零米标记，也就是主链路中点，' ;;
                *_off05_*) pos_label='零点五米标记，' ;;
                *_off10_*) pos_label='一米标记，' ;;
                *_posL_*)  pos_label='左侧 L 标记，' ;;
                *_posC_*)  pos_label='中心 C 标记，' ;;
                *_posR_*)  pos_label='右侧 R 标记，' ;;
                *)         pos_label='本条试验指定的地面标记，' ;;
            esac
            # 朝向随布局而变：多接收端+相机布局要求面向相机，
            # 单链路距离实验要求面向第一接收节点。朝向影响人体散射截面，不可混用。
            local facing_label
            case "$trial" in
                *_facen1_*)    facing_label='面向第一接收节点' ;;  # 朝向对照，优先级最高
                *eq4m*|*_mr_*) facing_label='面向相机' ;;
                *)             facing_label='面向第一接收节点' ;;
            esac
            case "$action_label" in
                walk_link2)
                    action_prompt='开始行走。请在第二条链路中点垂直来回穿越。' ;;
                arm_wave)
                    action_prompt='开始手臂动作。请进入第二条链路中点，面向相机站立，双脚保持不动，左右手臂交替抬起并挥动。' ;;
                leg_lift)
                    action_prompt='开始腿部动作。请进入第二条链路中点，面向相机站立，手臂自然保持，左右腿交替抬起。' ;;
                sit_to_stand)
                    action_prompt='开始坐下起立。请面向相机，连续完成坐下和起立动作。' ;;
                distance_static)
                    action_prompt="请进入${pos_label}${facing_label}，静止站立并正常呼吸。" ;;
                distance_motion)
                    action_prompt="请进入${pos_label}${facing_label}，持续原地踏步，双臂自然摆动。" ;;
                distance_plate_mid)
                    action_prompt='请把金属板垂直放到主链路中点，中心对齐天线高度，然后立即离开实验区域。'
                    end_prompt='请立即移走金属板，并离开实验区域。' ;;
                distance_plate_rx)
                    action_prompt='请把金属板放到第一接收节点天线前方五厘米处，然后立即离开实验区域。'
                    end_prompt='请立即移走金属板，并离开实验区域。' ;;
                *)
                    action_prompt='开始动作。请按计划执行。' ;;
            esac
            if [ "$action_label" = "multirx_fov" ]; then
                say -v Tingting '视野验证开始。请在场外等待第一个站位提示。' >/dev/null 2>&1 &
                ( sleep 15.2; say -v Tingting '请站到最靠近第一接收节点的左标记，面向相机，保持静止。不要继续向相机走。' ) >/dev/null 2>&1 &
                ( sleep 30.2; say -v Tingting '请移动到中间标记，面向相机，保持静止。' ) >/dev/null 2>&1 &
                ( sleep 45.2; say -v Tingting '请移动到最靠近相机和第三节点的右标记，面向相机，保持静止。不要越过标记。' ) >/dev/null 2>&1 &
                ( sleep 60.2; say -v Tingting '请留在右标记，缓慢抬起双臂一次，然后放下，并继续站在标记上。' ) >/dev/null 2>&1 &
                ( sleep 70.2; say -v Tingting '验证动作完成，请立即离开实验区域并关好门。' ) >/dev/null 2>&1 &
                ( sleep 80.2; say -v Tingting '请保持空场，不要进入。' ) >/dev/null 2>&1 &
            else
                say -v Tingting '记录已经开始。请保持空场，听到开始动作后再进入。' >/dev/null 2>&1 &
                ( sleep 5.2; say -v Tingting "$action_prompt" ) >/dev/null 2>&1 &
                ( sleep 22; say -v Tingting "$end_prompt" ) >/dev/null 2>&1 &
                ( sleep 25.2; say -v Tingting '请保持空场，不要进入。' ) >/dev/null 2>&1 &
            fi
        else
            warn "LOCAL_VOICE_PROTOCOL 已设置，但 Mac 上找不到 say"
        fi
    fi

    local tx_repeat; tx_repeat=$(kv TX_REPEAT "$tx_log")
    local tx_launch; tx_launch=$(kv TX_LAUNCH_SYSTEM_NS "$tx_log")
    local tx_rf_if; tx_rf_if=$(kv TX_RF_INTERFACE "$tx_log")
    local tx_freq; tx_freq=$(kv TX_FREQUENCY_MHZ "$tx_log")
    local tx_power; tx_power=$(kv TX_POWER_DBM_REPORTED "$tx_log")
    local tx_power_control; tx_power_control=$(kv TX_POWER_CONTROL "$tx_log")
    [ -n "$tx_repeat" ] || die "发射端日志缺少 TX_REPEAT"

    sleep $((dur + TX_MARGIN))
    agent "$TX_NODE" stop tx >"$dest/_tx_stop.log" 2>&1 || warn "发射端停止异常"
    TX_RUNNING=0
    log "发包结束，等待接收端与相机收尾落盘..."

    wait "$RX_A_PID" || { tail -n 25 "$rxa_log" >&2; die "$RX_A 采集失败"; }
    wait "$RX_B_PID" || { tail -n 25 "$rxb_log" >&2; die "$RX_B 采集失败"; }
    if [ -n "$CAM_HOST" ]; then
        wait "$CAM_PID" || { tail -n 25 "$cam_log" >&2; die "相机采集失败"; }
    fi

    # ---------------------------------------------------------- 回收
    local a_csi b_csi a_npz b_npz
    a_csi=$(kv CSIFILE "$rxa_log");  b_csi=$(kv CSIFILE "$rxb_log")
    a_npz=$(kv TARGETNPZ "$rxa_log"); b_npz=$(kv TARGETNPZ "$rxb_log")
    [ -n "$a_csi" ] || die "$RX_A 日志缺少 CSIFILE"
    [ -n "$b_csi" ] || die "$RX_B 日志缺少 CSIFILE"
    [ -n "$a_npz" ] || die "$RX_A 未生成 target_csi.npz"
    [ -n "$b_npz" ] || die "$RX_B 未生成 target_csi.npz"

    log "回收数据..."
    fetch "$RX_A" "$a_csi" "$dest/node1.csi"
    fetch "$RX_A" "$a_npz" "$dest/node1_target_csi.npz"
    fetch "$RX_B" "$b_csi" "$dest/node3.csi"
    fetch "$RX_B" "$b_npz" "$dest/node3_target_csi.npz"
    scp -q $SSH_OPTS "$RX_A:$(dirname "$a_csi")/metadata.txt" "$dest/node1_metadata.txt" 2>/dev/null
    scp -q $SSH_OPTS "$RX_B:$(dirname "$b_csi")/metadata.txt" "$dest/node3_metadata.txt" 2>/dev/null

    local cam_meta_local=""
    if [ -n "$CAM_HOST" ]; then
        local camdir; camdir=$(kv CAMDIR "$cam_log")
        [ -n "$camdir" ] || die "相机日志缺少 CAMDIR"
        log "回收相机数据（深度约 14 MB/s，百兆网需要一点时间）..."
        scp -q $SSH_OPTS "$CAM_HOST:$camdir/*" "$dest/" || die "相机数据回收失败"
        cam_meta_local=$(ls "$dest"/*_cam.json 2>/dev/null | head -1)
        [ -n "$cam_meta_local" ] || die "回收后找不到相机元数据"
    fi

    # ---------------------------------------------------------- 对齐
    log "包级对齐 + 导频剔除${CAM_HOST:+ + 相机配对}..."
    local align_log="$dest/alignment.log"
    "$PYTHON_BIN" "$ALIGN_PY" "$dest/node1.csi" "$dest/node3.csi" \
        --node1-target-npz "$dest/node1_target_csi.npz" \
        --node3-target-npz "$dest/node3_target_csi.npz" \
        --output-dir "$dest/aligned" --export-npz \
        ${cam_meta_local:+--cam-meta "$cam_meta_local"} \
        >"$align_log" 2>&1 || { cat "$align_log" >&2; die "对齐失败"; }
    sed 's/^/     /' "$align_log"

    local npz="$dest/aligned/aligned_csi.npz"
    [ -s "$npz" ] || die "对齐未生成 aligned_csi.npz"
    "$PYTHON_BIN" - "$npz" <<'PY' || die "aligned_csi.npz 完整性检查失败"
import sys
import numpy as np
with np.load(sys.argv[1], allow_pickle=False) as d:
    need = {"node1_csi", "node3_csi", "node1_frame_index", "node3_frame_index"}
    missing = need - set(d.files)
    if missing:
        raise SystemExit(f"缺少键: {sorted(missing)}")
    n1, n3 = d["node1_csi"].shape[0], d["node3_csi"].shape[0]
    if n1 == 0 or n1 != n3:
        raise SystemExit(f"行数异常: node1={n1} node3={n3}")
    if "node1_csi_data" in d.files and d["node1_csi_data"].shape[1] != 234:
        raise SystemExit("导频剔除后子载波数不是 234")
    print(f"完整性检查通过：{n1} 行")
PY

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
tx_rf_interface: ${tx_rf_if:-unknown}
frequency_mhz: ${tx_freq:-unknown}
txpower_dbm_reported: ${tx_power:-unknown}
txpower_control: ${tx_power_control:-unknown}
rx_gain_mode: AX210 firmware AGC, numeric gain not exposed
local_voice_protocol: ${LOCAL_VOICE_PROTOCOL:-none}
action_label: ${action_label:-none}
layout_id: ${LAYOUT_ID:-unknown}
node1_xyz_m: ${NODE1_XYZ_M:-unknown}
node2_xyz_m: ${NODE2_XYZ_M:-unknown}
node3_xyz_m: ${NODE3_XYZ_M:-unknown}
camera_xyz_m: ${CAMERA_XYZ_M:-unknown}
camera_orientation: ${CAMERA_ORIENTATION:-unknown}
layout_reference: ${LAYOUT_REFERENCE:-unknown}
chair_center_xyz_m: ${CHAIR_CENTER_XYZ_M:-not_applicable}
chair_orientation: ${CHAIR_ORIENTATION:-not_applicable}
chair_seat_height_m: ${CHAIR_SEAT_HEIGHT_M:-not_applicable}
chair_description: ${CHAIR_DESCRIPTION:-not_applicable}
chair_stability: ${CHAIR_STABILITY:-not_applicable}
started_at: $stamp
EOF

    TRIAL_OK=1
    c_grn "[lab] trial 完成 -> $dest"
    du -sh "$dest" | awk '{print "     体积 " $1}'
}

fetch() {
    local host="$1" remote="$2" local_path="$3"
    scp -q $SSH_OPTS "$host:$remote" "$local_path" || die "$host 回收 $remote 失败"
    local size; size=$(stat -f%z "$local_path" 2>/dev/null || echo 0)
    [ "$size" -gt 10240 ] || die "$local_path 仅 $size 字节，判定无效"
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
        names+=("$trial"); durs+=("$dur"); reps+=("$rep")
        total=$((total + rep))
    done < "$file"
    [ "${#names[@]}" -gt 0 ] || die "批量文件里没有有效条目"

    log "批量采集：${#names[@]} 种场景，共 $total 组"
    preflight

    # 批量过程中始终不恢复 Wi-Fi，否则每组都要等 NM 重连，且反复切换容易出错
    local saved_restore="$NO_RESTORE"
    NO_RESTORE=1

    local done_count=0 i r idx
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
                stop_all
            fi
        done
    done

    NO_RESTORE="$saved_restore"
    TRIAL_OK=1
    c_grn "[lab] 批量采集结束，共 $total 组"
    [ "$NO_RESTORE" = "1" ] || restore_all
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
        run_trial "$TRIAL" "$DUR"
        [ "$NO_RESTORE" = "1" ] && log "NO_RESTORE=1，保持采集状态（记得最后执行 csi_lab.sh restore）" || restore_all
        ;;
esac
