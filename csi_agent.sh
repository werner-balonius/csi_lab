#!/bin/bash
# csi_agent.sh <check|check-rx|check-cam|tx|rx|cam|stop|restore> [参数...]
#
# 节点端非交互脚本，供 Mac 上的 csi_lab.sh 通过 SSH 调用。放在三台节点的 ~/ 下。
#
# 沿革：本文件基于队友的 csi_node.sh（8-4 版，317 行），保留其全部核心逻辑
#       —— do_prep 的静默失败校验、do_stop 的 tx/rx 分离、do_restore 用
#       ethtool -P 取回硬件 MAC —— 这三处都是踩过坑换来的，不要改动。
#       新增部分仅为深度相机（cam / check-cam / stop cam）。
#
#   check          环境自检，不改变任何状态。失败返回非 0。
#   check-rx       在 check 之上，额外校验接收端出图/分析用的 Python 环境。
#   check-cam      校验 depthai 与 OAK 设备可用性（仅相机宿主节点需要）。
#   restore        删除 monitor 接口、还原 MAC、把网卡交还 NetworkManager，
#                  恢复该节点的正常 Wi-Fi 上网。
#   tx <s>         启动广播 injector；按 s×200 个包限定空口时长。
#   rx <trial> <s> 采集指定秒数，结果落在独立目录里。
#   cam <trial> <s> 采集深度+彩色，结果落在独立目录里。
#   stop tx|rx|cam  Tx 立即停止；Rx 用 SIGINT 优雅保存；Cam 用 SIGTERM 收尾。
#
# 相对旧版的关键修正：
#   1. Rx 用 SIGINT 等待落盘；Tx 无文件需保存，立即停止以免超时继续发包。
#   2. 每个 trial 用独立空目录，产出文件唯一确定；旧版靠"最新的 .csi"猜，会认错。
#   3. 每一步检查返回码；旧版无论成败都报成功。
#   4. array_prepare 只在 tx/rx 时执行；旧版连 stop 都会重置网卡。
#
# 重要：PicoScenes 本身必须以普通用户运行（不能 sudo PicoScenes）。网卡准备
# 工具内部以及 NetworkManager 恢复步骤会使用 sudo -n，因此相关系统命令必须
# 预先配置最小范围的 NOPASSWD；do_prep 会校验真实 monitor/信道状态。

set -uo pipefail

ROLE="${1:-}"
TRIAL="${2:-cap}"
DUR="${3:-30}"
TX_DURATION="${2:-30}"

PHY=1
CHANNEL="${CSI_CHANNEL:-2412 20}"
BCAST="FF:FF:FF:FF:FF:FF"          # 多接收端必须广播，单播会导致只有一台收到
PRESET="TX_CBW_20_HESU"
DELAY_US=5000                        # 200 pkt/s
PACKETS_PER_SECOND=$((1000000 / DELAY_US))

DATADIR="$HOME/csi_data"
NODE=$(cat "$HOME/.csi_node" 2>/dev/null || hostname -s)
PS_BIN=/usr/bin/PicoScenes
PREP_BIN=/usr/sbin/array_prepare_for_picoscenes
STATUS_BIN=/usr/sbin/array_status

if [ -n "${CSI_PYTHON:-}" ]; then
    PYTHON_BIN="$CSI_PYTHON"
elif [ -x "$HOME/csi_venv/bin/python3" ]; then
    PYTHON_BIN="$HOME/csi_venv/bin/python3"
else
    PYTHON_BIN=python3
fi

log()  { echo "[$NODE] $*"; }
fail() { echo "[$NODE] 错误: $*" >&2; exit 1; }

# ---------------------------------------------------------------- check
do_check() {
    local rc=0
    log "系统   $(lsb_release -ds 2>/dev/null) / $(uname -r)"

    local drv
    drv=$(dpkg -l 2>/dev/null | awk '/picoscenes-driver-modules/{print $2}' | head -1)
    if [ -z "$drv" ]; then
        log "驱动   未安装"; rc=1
    elif [[ "$drv" == *"$(uname -r)"* ]]; then
        log "驱动   $drv (与内核匹配)"
    else
        log "驱动   $drv 与内核 $(uname -r) 不匹配 -> 会报 Unresolvable device ID"; rc=1
    fi

    [ -x "$PS_BIN" ] || { log "PicoScenes 未找到"; rc=1; }

    local ax
    ax=$($STATUS_BIN 2>/dev/null | grep -E "^${PHY} ")
    if [ -n "$ax" ]; then
        log "AX210  $ax"
    else
        log "AX210  PhyPath $PHY 不可见"; rc=1
    fi

    local avail_kb
    avail_kb=$(df -k "$HOME" | awk 'NR==2{print $4}')
    log "磁盘   $((avail_kb/1024/1024)) GB 可用"
    [ "$avail_kb" -lt 2097152 ] && { log "磁盘不足 2 GB"; rc=1; }

    # PicoScenes 拒绝 root 运行。sudo 能力会在真正 do_prep 时连同网卡状态验证。
    if [ "$(id -u)" -eq 0 ]; then
        log "用户   当前是 root -> PicoScenes 会拒绝启动"; rc=1
    fi

    if pgrep -x PicoScenes >/dev/null; then
        log "警告   已有 PicoScenes 在运行"
    fi

    [ $rc -eq 0 ] && log "自检通过" || log "自检未通过"
    return $rc
}

do_check_rx() {
    do_check || return 1
    [ -f "$HOME/csi_plot.py" ] \
        || fail "缺少 $HOME/csi_plot.py"
    command -v "$PYTHON_BIN" >/dev/null 2>&1 \
        || fail "找不到 Python: $PYTHON_BIN"
    "$PYTHON_BIN" -c "import numpy,matplotlib,CSIKit" 2>/dev/null \
        || fail "接收端 Python 缺少 numpy/matplotlib/CSIKit；先安装采集分析环境"
    log "接收端分析环境已就绪：$PYTHON_BIN"
}

# 相机自检。注意不做 do_check —— 相机宿主不一定同时是收发节点。
do_check_cam() {
    [ -f "$HOME/csi_cam.py" ] || fail "缺少 $HOME/csi_cam.py"
    "$PYTHON_BIN" -c "import depthai" 2>/dev/null \
        || fail "缺少 depthai；在该节点执行 pip install depthai"
    local out
    out=$("$PYTHON_BIN" "$HOME/csi_cam.py" --check 2>&1 | grep -E '^CAM_')
    [ -n "$out" ] || fail "csi_cam.py --check 无输出，相机可能未连接或 udev 规则缺失"
    echo "$out" | grep -q '^CAM_OK=1' \
        || fail "未发现 OAK 设备。检查 USB 连接，以及 /etc/udev/rules.d/80-movidius.rules"
    echo "$out" | sed "s/^/[$NODE] /"
    # 磁盘：深度约 14 MB/s，30 秒一组约 460 MB，批量采集很容易撑爆
    local avail_kb
    avail_kb=$(df -k "$HOME" | awk 'NR==2{print $4}')
    [ "$avail_kb" -lt 5242880 ] \
        && log "警告: 剩余空间不足 5 GB，深度数据约 14 MB/s，批量采集前请清理"
    return 0
}

# ---------------------------------------------------------------- prep
# array_prepare_for_picoscenes 内部会调用 sudo ifconfig / iw / route。
# 一旦这些 sudo 因缺少 NOPASSWD 而失败，它【仍然会打印 "Preparation is done."
# 并返回 0】。因此绝不能相信它的退出码，必须回头验证网卡的真实状态。
# 这个静默失败曾导致 node3 全程停留在 managed 模式、5 GHz 信道，
# 25 秒只收到 141 帧，而编排脚本一路报成功。
do_prep() {
    # 必须先让 NetworkManager 交出 AX210。否则它会持续维持 Wi-Fi 连接，
    # array_prepare 设置 monitor 频率时被顶掉，结果是 mon 接口存在但无信道，
    # 该节点几乎收不到任何包——而整个过程不报任何错误。
    local wif
    wif=$(ls /sys/class/net 2>/dev/null | grep -E '^wl' | head -1)
    if [ -n "$wif" ] && command -v nmcli >/dev/null 2>&1; then
        sudo -n nmcli device set "$wif" managed no >/dev/null 2>&1 || true
        sleep 1
    fi

    "$PREP_BIN" "$PHY" "$CHANNEL" >/tmp/prep.log 2>&1 || true

    if grep -qiE "sudo:.*(password|密码)" /tmp/prep.log; then
        fail "array_prepare 内部 sudo 需要密码。请在该节点配置 NOPASSWD：ifconfig / iw / route"
    fi

    # 真实状态校验：必须存在 monitor 接口，且落在目标信道上
    local want_mhz mon_line cur_mhz
    want_mhz=$(echo "$CHANNEL" | awk '{print $1}')
    mon_line=$(iw dev 2>/dev/null | awk '/Interface/{i=$2} /type monitor/{print i}' | head -1)
    [ -n "$mon_line" ] \
        || fail "未创建 monitor 接口，网卡仍处于 managed 模式（array_prepare 静默失败）"

    cur_mhz=$(iw dev "$mon_line" info 2>/dev/null | awk '/channel/{print $3}' | tr -d '(')
    [ "$cur_mhz" = "$want_mhz" ] \
        || fail "monitor 接口 $mon_line 在 ${cur_mhz:-未知} MHz，期望 ${want_mhz} MHz"

    log "monitor 接口 $mon_line @ ${cur_mhz} MHz 已就绪"
}

# 读取 array_prepare 后的真实射频状态。AX210 的接收增益由固件 AGC 管理，
# PicoScenes 的 --rx-gain 只适用于 QCA9300，不能把该参数误记为 AX210 设置。
rf_state() {
    RF_INTERFACE=$(iw dev 2>/dev/null | awk '/Interface/{i=$2} /type monitor/{print i; exit}')
    RF_FREQUENCY_MHZ=""
    RF_TXPOWER_DBM=""
    if [ -n "$RF_INTERFACE" ]; then
        RF_FREQUENCY_MHZ=$(iw dev "$RF_INTERFACE" info 2>/dev/null \
            | awk '/channel/{gsub(/[()]/, "", $3); print $3; exit}')
        RF_TXPOWER_DBM=$(iw dev "$RF_INTERFACE" info 2>/dev/null \
            | awk '/txpower/{print $2; exit}')
    fi
}

# ---------------------------------------------------------------- stop
# do_stop [tx|rx]
# 接收端必须优雅退出，否则 .csi 不落盘；发射端没有文件要保存，强杀无害。
do_stop() {
    local mode="${1:-rx}"

    # 相机是独立的 Python 进程，与 PicoScenes 无关，先单独处理
    if [ "$mode" = "cam" ]; then
        if ! pgrep -f "csi_cam.py" >/dev/null; then
            log "没有运行中的相机采集"
            return 0
        fi
        # csi_cam.py 捕获 SIGTERM 后会收尾落盘并写元数据，必须给足时间
        pkill -TERM -f "csi_cam.py" 2>/dev/null || true
        local cam_i
        for cam_i in $(seq 1 40); do
            pgrep -f "csi_cam.py" >/dev/null || { log "相机采集已停止 (${cam_i}x0.5s)"; return 0; }
            sleep 0.5
        done
        pkill -KILL -f "csi_cam.py" 2>/dev/null || true
        log "警告: 相机进程 20 秒未退出，已强制终止，本次深度数据可能不完整"
        return 1
    fi

    if ! pgrep -x PicoScenes >/dev/null; then
        log "没有运行中的 PicoScenes"
        return 0
    fi
    if [ "$mode" = "tx" ]; then
        # injector 没有需要落盘的数据。它会忽略 SIGINT 并在旧逻辑中继续
        # 发包约 15 秒，所以 Tx 必须立即终止。
        pkill -KILL -x PicoScenes 2>/dev/null || true
        local tx_i
        for tx_i in $(seq 1 20); do
            pgrep -x PicoScenes >/dev/null || { log "injector 已立即停止"; return 0; }
            sleep 0.1
        done
        fail "无法停止 injector"
    fi

    # logger 必须用 SIGINT，收到后才会正常保存 .csi
    pkill -INT -x PicoScenes 2>/dev/null || true
    local i
    for i in $(seq 1 30); do
        pgrep -x PicoScenes >/dev/null || { log "PicoScenes 已退出 (${i}x0.5s)"; return 0; }
        sleep 0.5
    done
    pkill -KILL -x PicoScenes 2>/dev/null || true
    sleep 1
    log "警告: logger 在 SIGINT 后 15 秒仍未退出，已强制终止，本次数据可能不完整"
    return 1
}

# ---------------------------------------------------------------- restore
# 采集结束后把 AX210 还给 NetworkManager，恢复节点的正常 Wi-Fi 上网。
# 顺序不能反：先删 monitor 接口，再恢复 MAC，最后才交还 NM。
do_restore() {
    do_stop rx >/dev/null 2>&1

    local m
    for m in $(iw dev 2>/dev/null | grep -oE 'mon[0-9]+' | sort -u); do
        sudo -n iw dev "$m" del 2>/dev/null || true
    done

    local wif perm
    wif=$(ls /sys/class/net 2>/dev/null | grep -E '^wl' | head -1)
    if [ -n "$wif" ]; then
        # array_prepare 会把主接口 MAC 改成 PicoScenes 默认值，必须用
        # ethtool -P 读回固化在硬件里的原始 MAC 才能真正还原
        perm=$(ethtool -P "$wif" 2>/dev/null | grep -oiE '([0-9a-f]{2}:){5}[0-9a-f]{2}')
        sudo -n ip link set "$wif" down 2>/dev/null || true
        [ -n "$perm" ] && sudo -n ip link set "$wif" address "$perm" 2>/dev/null || true
        sudo -n ip link set "$wif" up 2>/dev/null || true
        sudo -n nmcli device set "$wif" managed yes 2>/dev/null || true
    fi

    sudo -n systemctl restart NetworkManager >/dev/null 2>&1 || true
    sleep 4

    local st
    st=$(nmcli -t -f DEVICE,STATE device status 2>/dev/null | grep "^${wif}:" | cut -d: -f2)
    log "Wi-Fi 已归还 NetworkManager：$wif 状态=${st:-未知}（自动重连需数秒）"
}

# ---------------------------------------------------------------- tx
do_tx() {
    [[ "$TX_DURATION" =~ ^[0-9]+$ ]] && [ "$TX_DURATION" -gt 0 ] \
        || fail "Tx 时长必须是正整数秒，收到 '$TX_DURATION'"
    local repeat_count=$((TX_DURATION * PACKETS_PER_SECOND))

    do_stop tx >/dev/null 2>&1
    do_prep
    rf_state
    local launch_ns
    launch_ns=$(date +%s%N)
    "$PS_BIN" "-d debug -i $PHY --mode injector --preset $PRESET \
--repeat $repeat_count --delay $DELAY_US --target-mac-address $BCAST" >/tmp/tx.log 2>&1 &
    sleep 1
    pgrep -x PicoScenes >/dev/null \
        || fail "injector 启动后立即退出，详见节点 /tmp/tx.log$(printf '\n'; tail -5 /tmp/tx.log)"
    log "injector 运行中：${repeat_count} 包 × ${DELAY_US}us，目标空口时长 ${TX_DURATION}s"
    echo "TX_LAUNCH_SYSTEM_NS=$launch_ns"
    echo "TX_REPEAT=$repeat_count"
    echo "TX_DELAY_US=$DELAY_US"
    echo "TX_RF_INTERFACE=${RF_INTERFACE:-unknown}"
    echo "TX_FREQUENCY_MHZ=${RF_FREQUENCY_MHZ:-unknown}"
    echo "TX_POWER_DBM_REPORTED=${RF_TXPOWER_DBM:-unknown}"
    echo "TX_POWER_CONTROL=AX210_firmware_regulatory"
}

# ---------------------------------------------------------------- rx
do_rx() {
    [[ "$DUR" =~ ^[0-9]+$ ]] || fail "时长必须是整数秒，收到 '$DUR'"

    do_stop rx >/dev/null 2>&1
    do_prep
    rf_state

    # 每个 trial 独立空目录 -> 产出文件唯一，不需要靠时间戳猜
    local stamp rundir
    stamp=$(date +%Y%m%d_%H%M%S)
    rundir="$DATADIR/${stamp}_${TRIAL}"
    mkdir -p "$rundir" || fail "无法创建 $rundir"
    cd "$rundir" || fail "无法进入 $rundir"

    log "开始采集 ${DUR}s -> $rundir"
    "$PS_BIN" "-d debug -i $PHY --mode logger" >"$rundir/logger.log" 2>&1 &
    sleep 2
    pgrep -x PicoScenes >/dev/null \
        || fail "logger 启动后立即退出$(printf '\n'; tail -5 "$rundir/logger.log")"

    sleep "$DUR"
    do_stop rx || log "警告: 停止过程异常"

    local csi
    csi=$(find "$rundir" -maxdepth 1 -name '*.csi' -type f | head -1)
    [ -n "$csi" ] || fail "采集结束但没有产生 .csi$(printf '\n'; tail -5 "$rundir/logger.log")"

    local out="$rundir/${NODE}_${TRIAL}.csi"
    [ "$csi" != "$out" ] && mv "$csi" "$out"

    local sz
    sz=$(stat -c %s "$out" 2>/dev/null || echo 0)
    [ "$sz" -gt 10240 ] || fail "产出文件仅 ${sz} 字节，判定为无效采集"

    # 元数据随数据同目录保存，避免事后无法追溯
    cat > "$rundir/metadata.txt" <<EOF
trial_name: $TRIAL
node: $NODE
host: $(hostname)
role: rx
date: $(date -Iseconds)
channel: $CHANNEL
preset: $PRESET
tx_delay_us: $DELAY_US
target_mac: $BCAST
duration_s: $DUR
kernel: $(uname -r)
rf_interface: ${RF_INTERFACE:-unknown}
frequency_mhz: ${RF_FREQUENCY_MHZ:-unknown}
local_interface_txpower_dbm_reported: ${RF_TXPOWER_DBM:-unknown}
rx_gain_mode: AX210_firmware_AGC_not_exposed
picoscenes_rx_gain_option: unsupported_for_AX210
file: $out
size_bytes: $sz
EOF

    log "采集完成 $(basename "$out")  $((sz/1024/1024)) MB"

    # 本地出图与摘要。失败只告警，不影响已保存的 .csi。
    # 注意 csi_plot.py 逐帧取 csi_matrix 并按"发送源 MAC+子载波数"筛选，
    # 不使用 csitools.get_CSI()，以规避首帧定尺寸导致的静默截断。
    if [ -f "$HOME/csi_plot.py" ] && "$PYTHON_BIN" -c "import numpy,matplotlib,CSIKit" 2>/dev/null; then
        if "$PYTHON_BIN" "$HOME/csi_plot.py" "$out" 2>&1 | grep -v "Warning\|warn" | sed "s/^/[$NODE]   /"; then
            :
        else
            log "警告: 出图失败，但 .csi 已正常保存"
        fi
    else
        log "跳过出图（缺 csi_plot.py 或 numpy/matplotlib/CSIKit）"
    fi

    local target_npz="${out%.csi}_target_csi.npz"
    if [ -s "$target_npz" ]; then
        printf 'target_npz: %s\ntarget_npz_size_bytes: %s\n' \
            "$target_npz" "$(stat -c %s "$target_npz" 2>/dev/null || echo 0)" \
            >> "$rundir/metadata.txt"
    fi
    echo "RUNDIR=$rundir"
    echo "CSIFILE=$out"
    [ -s "$target_npz" ] && echo "TARGETNPZ=$target_npz"
}

# ---------------------------------------------------------------- cam
# 相机采集。与 rx 各自建立独立目录：相机宿主可能同时在跑 rx，
# 两者时间戳不同、产物不同，混在一个目录里事后难以追溯。
do_cam() {
    [[ "$DUR" =~ ^[0-9]+$ ]] && [ "$DUR" -gt 0 ] \
        || fail "相机时长必须是正整数秒，收到 '$DUR'"
    [ -f "$HOME/csi_cam.py" ] || fail "缺少 $HOME/csi_cam.py"

    do_stop cam >/dev/null 2>&1

    local stamp rundir
    stamp=$(date +%Y%m%d_%H%M%S)
    rundir="$DATADIR/${stamp}_${TRIAL}_cam"
    mkdir -p "$rundir" || fail "无法创建 $rundir"

    log "开始相机采集 ${DUR}s -> $rundir"
    # 必须流式输出：编排端靠 CAM_READY=1 判断相机已进入采集状态，
    # 若用 out=$(...) 缓冲，该信号要等采集结束才出现，编排端会误判超时。
    # -u 关闭 Python 输出缓冲，grep/sed 用行缓冲，确保逐行实时送达。
    local rc camlog="$rundir/cam.log"
    "$PYTHON_BIN" -u "$HOME/csi_cam.py" \
        --trial "$TRIAL" --duration "$DUR" \
        --outdir "$rundir" --node "$NODE" 2>&1 \
        | tee "$camlog" \
        | grep --line-buffered -E '^CAM_' \
        | sed -u "s/^/[$NODE] /"
    rc=${PIPESTATUS[0]}

    if [ "$rc" -ne 0 ]; then
        grep -vE '^\[20[0-9][0-9]|^CAM_' "$camlog" | tail -5 >&2
        fail "相机采集失败（退出码 $rc）"
    fi

    local depth meta
    depth=$(awk -F= '/^CAM_DEPTH=/{print $2}' "$camlog")
    meta=$(awk -F= '/^CAM_META=/{print $2}' "$camlog")
    [ -s "$depth" ] || fail "深度文件缺失或为空: $depth"
    [ -s "$meta" ]  || fail "相机元数据缺失: $meta"

    echo "CAMDIR=$rundir"
}

# ---------------------------------------------------------------- main
case "$ROLE" in
    check)     do_check ;;
    check-rx)  do_check_rx ;;
    check-cam) do_check_cam ;;
    restore)   do_restore ;;
    tx)        do_tx ;;
    rx)        do_rx ;;
    cam)       do_cam ;;
    stop)      do_stop "${2:-rx}" ;;
    *)  echo "用法: csi_agent.sh check|check-rx|check-cam|tx <秒>|rx <trial> <秒>|cam <trial> <秒>|stop tx|rx|cam|restore" >&2
        exit 1 ;;
esac
