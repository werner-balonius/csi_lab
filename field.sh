#!/bin/bash
# field.sh — 现场实验引导脚本（在 Mac 上运行）
#
#   ./field.sh          引导式完整流程（推荐，按提示一步步走）
#   ./field.sh net      只做网络诊断与恢复
#   ./field.sh pack     显示装备清单
#
# 设计意图
# --------
# 现场容易在压力下漏步骤。本脚本把「布局记录」做成流程中不可跳过的一环——
# 布局未记录是从 2026-07-31 拖到现在的唯一实质阻塞，不是软件问题，
# 但没有它整批数据都失去可比性。

set -uo pipefail

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
LAB="$SCRIPT_DIR/csi_lab.sh"
CONFIG_FILE="${CSI_LAB_CONFIG:-$SCRIPT_DIR/csi_lab.conf}"
[ -f "$CONFIG_FILE" ] && . "$CONFIG_FILE"
RESULTS="${CSI_RESULTS:-$HOME/csi_results}"
MGMT_NET="192.168.50"
MAC_IP="$MGMT_NET.1"
CAM_HOST="${CAM_HOST:-node1}"

# 节点清单：名称 / IP / 登录用户（用索引数组，bash 3.2 无关联数组）
NODES="node1 node2 node3"
IP_node1="$MGMT_NET.11"; USER_node1="werner"
IP_node2="$MGMT_NET.12"; USER_node2="alfonse"
IP_node3="$MGMT_NET.13"; USER_node3="kaminashi"

SESSION=$(date +%Y%m%d_%H%M%S)
LOGDIR="$RESULTS/_field_$SESSION"

c_red() { printf '\033[31m%s\033[0m\n' "$*"; }
c_grn() { printf '\033[32m%s\033[0m\n' "$*"; }
c_ylw() { printf '\033[33m%s\033[0m\n' "$*"; }
c_cyn() { printf '\033[36m%s\033[0m\n' "$*"; }
hr()    { printf '%s\n' "------------------------------------------------------------"; }
step()  { echo; hr; c_cyn "  $*"; hr; }
ok()    { c_grn "  [OK] $*"; }
bad()   { c_red "  [!!] $*"; }
note()  { printf '  %s\n' "$*"; }

ask() {
    # ask "问题" -> 返回 0 表示 yes
    local reply
    printf '\n  %s [y/N] ' "$1"
    read -r reply
    case "$reply" in [yY]*) return 0 ;; *) return 1 ;; esac
}

pause_for() {
    printf '\n  %s，完成后按回车继续... ' "$1"
    read -r _
}

ipget() { eval "printf '%s' \"\$IP_$1\""; }
usrget() { eval "printf '%s' \"\$USER_$1\""; }

# ==================================================================== 装备清单
show_pack() {
    step "装备清单"
    cat <<'EOF'
  必带（缺任何一项管理网都起不来）:
    [ ] USB 网卡  ——  必须是原来那一个，MAC 00:e0:4c:94:b8:97
        Mac 的 192.168.50.1 绑定在这块网卡上，换一块就要重新配
    [ ] 交换机 + 交换机电源
    [ ] 网线 x4（Mac 一根，三台节点各一根）
    [ ] 三台节点的电源适配器
    [ ] OAK-D-W 相机 + 原装 USB 线

  强烈建议:
    [ ] 卷尺或激光测距（5 / 10 / 15 m 要量准，凭感觉摆没有意义）
    [ ] 有源 USB Hub（相机供电余量小，可防硬复位）
    [ ] 记号笔 + 胶带（在地面标出节点位置与行走路径，换档时能复位）

  可选:
    [ ] 排插 / 延长线
    [ ] 备用网线（网线故障排查起来最费时间）
EOF
}

# ==================================================================== 网络
mac_iface() {
    # 找出携带 192.168.50.1 的接口名（名字可能不是 en5）
    ifconfig 2>/dev/null | awk -v ip="$MAC_IP" '
        /^[a-z0-9]+:/ { iface=substr($1,1,length($1)-1) }
        $1=="inet" && $2==ip { print iface; exit }'
}

net_check() {
    step "网络诊断"
    local fail=0

    # --- Mac 侧 ---
    local iface; iface=$(mac_iface)
    if [ -n "$iface" ]; then
        ok "Mac 管理网就绪：$iface = $MAC_IP"
    else
        bad "Mac 上没有任何接口持有 $MAC_IP"
        fail=1
        note ""
        note "  最常见原因：换了一块 USB 网卡。"
        note "  macOS 把 IP 配置绑定在「网络服务」上，而服务绑定网卡 MAC；"
        note "  换网卡会新建一个走 DHCP 的服务，于是拿不到 $MAC_IP。"
        note ""
        note "  检查当前识别到的以太网硬件："
        networksetup -listallhardwareports 2>/dev/null \
            | grep -A2 -iE "LAN|Ethernet" | sed 's/^/      /'
        note ""
        note "  若确认用的是原网卡但仍无 IP，手动恢复："
        note "      sudo networksetup -setmanual \"USB 10/100/1000 LAN\" $MAC_IP 255.255.255.0 \"\""
        note "  若用的是新网卡，把上面的服务名换成 networksetup -listallnetworkservices 里的对应名字。"
        echo
        if ask "要现在尝试自动恢复吗（需要输入 Mac 管理员密码）"; then
            local svc
            svc=$(networksetup -listallnetworkservices 2>/dev/null | grep -iE "USB.*LAN|LAN.*USB" | head -1)
            if [ -n "$svc" ]; then
                note "  对服务 \"$svc\" 应用静态地址..."
                sudo networksetup -setmanual "$svc" "$MAC_IP" 255.255.255.0 "" \
                    && sleep 3 && iface=$(mac_iface)
                [ -n "$iface" ] && { ok "已恢复：$iface = $MAC_IP"; fail=0; } || bad "恢复失败"
            else
                bad "找不到名字里含 USB/LAN 的网络服务，需要手动处理"
            fi
        fi
    fi

    # --- 链路 ---
    if [ -n "$iface" ]; then
        local status
        status=$(ifconfig "$iface" 2>/dev/null | awk '/status:/{print $2}')
        if [ "$status" = "active" ]; then
            ok "网线链路 active"
        else
            bad "网线链路 ${status:-未知}（网线没插好 / 交换机没通电 / 网线坏）"
            fail=1
        fi
    fi

    # --- 节点 ---
    echo
    local n ip unreachable=""
    for n in $NODES; do
        ip=$(ipget "$n")
        if ping -c 2 -W 1500 "$ip" >/dev/null 2>&1; then
            if ssh -o BatchMode=yes -o ConnectTimeout=6 "$n" true 2>/dev/null; then
                ok "$n ($ip) ping 通，SSH 通"
            else
                bad "$n ($ip) ping 通但 SSH 不通"
                note "      主机密钥变了或 sshd 没起来。先试："
                note "      ssh $(usrget "$n")@$ip"
                fail=1
            fi
        else
            bad "$n ($ip) 不可达"
            unreachable="$unreachable $n"
            fail=1
        fi
    done

    # --- 失联节点的进一步定位 ---
    if [ -n "$unreachable" ]; then
        echo
        c_ylw "  有节点失联，按以下顺序排查："
        note "    1. 该节点是否开机、是否已完成启动（等 1 分钟）"
        note "    2. 网线两端是否插紧，交换机对应口的指示灯是否亮"
        note "    3. 换一根网线试（网线是最常见的故障源）"
        note "    4. 换交换机上的另一个口"
        echo
        if ask "要扫描 $MGMT_NET.0/24 看看节点是否跑到了别的地址吗"; then
            note "  扫描中（约 20 秒）..."
            local i
            for i in $(seq 2 40); do
                ping -c 1 -W 200 "$MGMT_NET.$i" >/dev/null 2>&1 &
            done
            wait
            echo
            note "  当前 ARP 表里的 $MGMT_NET 网段："
            arp -an 2>/dev/null | grep "$MGMT_NET" | sed 's/^/      /' \
                || note "      （空）"
            note ""
            note "  若发现节点出现在意外地址，说明它的 NetworkManager 配置被改过。"
            note "  在该节点本机执行以下命令恢复（以 node1 为例）："
            note "      nmcli con mod mgmt ipv4.method manual \\"
            note "            ipv4.addresses $MGMT_NET.11/24 ipv4.never-default yes"
            note "      nmcli con up mgmt"
        fi
    fi

    echo
    if [ "$fail" = "0" ]; then
        c_grn "  网络全部就绪"
        return 0
    fi
    c_red "  网络存在问题，解决后再继续"
    return 1
}

# ==================================================================== 布局记录
record_layout() {
    local dist="$1"
    local file="$LOGDIR/layout_${dist}.txt"
    mkdir -p "$LOGDIR"

    step "记录布局 —— 间距 ${dist}"
    c_ylw "  这一步不能跳过。"
    note "  布局没有记录的话，walk_link1 与 walk_link2 之间无法比较，"
    note "  三个距离档之间也无法比较，整批数据的价值会大幅下降。"
    echo
    note "  请依次输入（直接回车表示留空，但不建议）："

    local tx_pos rx1_pos rx3_pos cam_pos cam_h path notes
    printf '\n  node2 (Tx) 位置描述: ';        read -r tx_pos
    printf '  node1 (Rx1, 相机宿主) 位置: ';   read -r rx1_pos
    printf '  node3 (Rx2) 位置: ';             read -r rx3_pos
    printf '  相机位置与朝向: ';               read -r cam_pos
    printf '  相机离地高度 (m): ';             read -r cam_h
    printf '  行走路径（起点→终点）: ';        read -r path
    printf '  天线朝向 / 其他备注: ';          read -r notes

    cat > "$file" <<EOF
# 布局记录 —— 间距 ${dist}
# 生成时间: $(date -Iseconds)

距离档:            ${dist}
node2 (Tx):        ${tx_pos}
node1 (Rx1/相机):  ${rx1_pos}
node3 (Rx2):       ${rx3_pos}
相机位置与朝向:    ${cam_pos}
相机离地高度_m:    ${cam_h}
行走路径:          ${path}
天线朝向与备注:    ${notes}
EOF

    ok "已保存 $file"
    note "  建议同时拍一张现场照片，和这个文件放在一起。"
    echo
    note "  另外提醒：相机与行走区应当【固定不动】，只移动 node2 改变间距。"
    note "  立体深度误差随距离平方增长，人应始终在离相机 5 m 以内。"
}

# ==================================================================== 各阶段
stage_preflight() {
    step "阶段 1：三台节点预检"
    CAM_HOST="" "$LAB" check 2>&1 | sed 's/^/  /'
    return "${PIPESTATUS[0]}"
}

stage_camera() {
    step "阶段 2：相机就位"
    c_ylw "  相机现在应当接在 $CAM_HOST（接收端），不是 node2。"
    note "  原因：接收端方案下深度与该端 CSI 天然同钟；"
    note "  接 node2 则它只发不收、没有 system_ns 记录，时钟偏差无法测量。"
    echo
    note "  注意 $CAM_HOST 从未接过相机，这是唯一没在真机验证过的环节。"
    pause_for "把相机接到 $CAM_HOST 并确认 USB 线插紧"

    local i
    for i in 1 2 3; do
        note "  第 $i 次尝试..."
        if ssh -o BatchMode=yes "$CAM_HOST" 'bash ~/csi_agent.sh check-cam' 2>&1 | sed 's/^/    /'; then
            ok "相机在 $CAM_HOST 上可用"
            return 0
        fi
        bad "检测失败"
        if [ "$i" -lt 3 ]; then
            note "    可能原因：USB 线只能充电、设备仍在重新枚举、udev 规则未生效"
            pause_for "拔插一次相机，等 5 秒"
        fi
    done
    return 1
}

stage_probe() {
    step "阶段 3：单组试采（确认全链路，再进入正式采集）"
    local name="probe_$(date +%H%M)"
    note "  将采集一组 30 秒，trial 名 $name"
    if ! ask "现在开始"; then note "  已跳过"; return 0; fi

    CAM_HOST="$CAM_HOST" "$LAB" "$name" 30 2>&1 | sed 's/^/  /'
    local rc="${PIPESTATUS[0]}"
    echo
    if [ "$rc" != "0" ]; then
        bad "试采失败，不要继续正式采集"
        return 1
    fi
    c_ylw "  请人工确认上面输出的四项："
    note "    1. 匹配率 NODE1/NODE3_MATCH_RATIO 均 > 0.95"
    note "    2. DATA_SUBCARRIERS = ${CSI_EXPECTED_SUBCARRIERS:-所选配置值}"
    note "    3. CAM_SEQ_GAPS = 0"
    note "    4. VALIDATION_OK = 1"
    ask "四项都正常吗" || return 1
    ok "试采通过"
}

stage_batch() {
    local dist="$1"
    local file="$SCRIPT_DIR/phase_c_${dist}.txt"
    step "正式采集 —— 间距 ${dist}"

    [ -f "$file" ] || { bad "找不到 $file"; return 1; }

    pause_for "把 node2 摆到 ${dist} 位置并用卷尺量准（相机与行走区保持不动）"
    record_layout "$dist"

    echo
    note "  即将采集 9 组，每组 30 秒，预计约 25 分钟。"
    note "  每组之间有 10 秒准备时间，就位后按回车可提前开始。"
    ask "开始 ${dist} 档采集" || { note "  已跳过"; return 0; }

    CAM_HOST="$CAM_HOST" "$LAB" batch "$file" 2>&1 | sed 's/^/  /'
    local rc="${PIPESTATUS[0]}"

    echo
    note "  本档结果："
    ls -d "$RESULTS"/*_"${dist}"_* 2>/dev/null | sed 's/^/    /' | tail -12
    local count
    count=$(ls -d "$RESULTS"/*"${dist}"* 2>/dev/null | grep -v _field_ | wc -l | tr -d ' ')
    note "  共 $count 个结果目录（期望 9）"

    echo
    c_ylw "  清理节点侧数据以腾出空间（相机宿主磁盘余量有限）："
    note "    ssh $CAM_HOST 'rm -rf ~/csi_data/*'"
    if ask "确认 Mac 已收到全部数据，现在清理节点侧"; then
        local n
        for n in $NODES; do
            ssh -o BatchMode=yes "$n" 'rm -rf ~/csi_data/*' 2>/dev/null \
                && ok "$n 已清理"
        done
    fi
    return "$rc"
}

# ==================================================================== 引导流程
guided() {
    mkdir -p "$LOGDIR"
    step "现场实验引导 —— $SESSION"
    note "  日志目录: $LOGDIR"
    note "  相机宿主: $CAM_HOST"
    echo
    note "  流程：网络 → 预检 → 相机 → 试采 → 5m → 10m → 15m"
    note "  任何一步失败都会停下来，不会带着问题往下跑。"

    show_pack
    ask "装备齐全，继续" || { note "  已中止"; exit 0; }

    net_check          || { bad "网络未就绪，中止"; exit 1; }
    stage_preflight    || { bad "节点预检失败，中止"; exit 1; }
    stage_camera       || { bad "相机不可用，中止"; exit 1; }
    stage_probe        || { bad "试采未通过，中止"; exit 1; }

    local d
    for d in 05m 10m 15m; do
        if ! stage_batch "$d"; then
            bad "${d} 档采集异常"
            ask "仍要继续下一档吗" || break
        fi
    done

    step "收尾"
    note "  恢复三台 Wi-Fi..."
    "$LAB" restore 2>&1 | sed 's/^/  /'
    echo
    note "  结果目录: $RESULTS"
    note "  布局记录: $LOGDIR"
    echo
    c_ylw "  离场前建议再做一件事："
    note "    在相机视野内挥手，同时用身体遮断 node2→node1 链路，"
    note "    制造一个两个模态都能看到的物理事件。"
    note "    软件时间对得上不代表物理上对得上，这是唯一的端到端验证手段。"
    echo
    c_grn "  今日流程结束"
}

case "${1:-}" in
    net)  net_check ;;
    pack) show_pack ;;
    ''|run|guided) guided ;;
    *) echo "用法: ./field.sh [net|pack]" >&2; exit 1 ;;
esac
