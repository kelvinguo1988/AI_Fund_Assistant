#!/usr/bin/env bash
# ============================================================================
# 镜像清理 — 只删本项目的历史构建，保留在用的与最近 N 个版本
#
# 背景（2026-11-26）：本项目每次部署都 `docker build` 出一个新 tag，
# NAS 上累积到 39 个镜像 / 7.9GB，其中 83% 可回收。手工清理容易漏，
# 也容易误删别的项目的镜像，所以固化成脚本。
#
# 安全原则（按顺序生效，任一不满足就不删）：
#   1. 只处理本项目的镜像名（默认 backend / frontend），绝不碰其他项目
#   2. 正在被容器使用的镜像一律跳过（docker ps -a 覆盖已停止容器）
#   3. 每个镜像名保留最近 KEEP 个版本（默认 2），留出回滚余地
#   4. 删除后跑一次悬空层清理，回收共享层
#
# 兼容性：只用 POSIX 基础语法（macOS 自带 bash 3.2 / NAS 的旧 bash /
# busybox sh 都能跑），不用 mapfile、关联数组、${var,,} 等 bash4+ 特性。
#
# 用法:
#   ./scripts/prune_images.sh              # 默认保留最近 2 个
#   ./scripts/prune_images.sh 3            # 保留最近 3 个
#   DRY_RUN=1 ./scripts/prune_images.sh    # 只看会删什么，不真删
# ============================================================================

set -u

KEEP="${1:-2}"
DRY_RUN="${DRY_RUN:-0}"

# 本项目镜像的 Repository 匹配规则（不匹配的一律不碰）
PATTERNS="${PRUNE_PATTERNS:-^(backend|frontend)$|/ai_fund_assistant/(backend|frontend)$}"

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; BLUE='\033[0;34m'; NC='\033[0m'
info() { printf "${BLUE}[prune]${NC} %s\n" "$*"; }
ok()   { printf "${GREEN}[prune]${NC} %s\n" "$*"; }
warn() { printf "${YELLOW}[prune]${NC} %s\n" "$*"; }

command -v docker >/dev/null 2>&1 || { warn "未检测到 docker，跳过镜像清理"; exit 0; }

TMPDIR_P="$(mktemp -d 2>/dev/null || mktemp -d -t prune)"
trap 'rm -rf "$TMPDIR_P"' EXIT

# ── 收集正在使用的镜像 ID（含已停止容器，防止删掉待重启的镜像）──────────
IN_USE_FILE="$TMPDIR_P/in_use"
: > "$IN_USE_FILE"
for cid in $(docker ps -aq 2>/dev/null); do
    # docker inspect 给的是完整 "sha256:xxxx..."，而 `docker images` 的 {{.ID}}
    # 是 12 位短 ID —— 必须统一截断，否则两边永远匹配不上（2026-11-26 踩过：
    # 在用镜像没被识别，差点把正在跑的 backend 删掉）。
    full=$(docker inspect --format '{{.Image}}' "$cid" 2>/dev/null) || continue
    printf '%s\n' "${full#sha256:}" | cut -c1-12 >> "$IN_USE_FILE"
done
sort -u "$IN_USE_FILE" -o "$IN_USE_FILE"

is_in_use() {
    [ -s "$IN_USE_FILE" ] && grep -qx "$(printf '%s' "$1" | cut -c1-12)" "$IN_USE_FILE"
}

# ── 列出候选：repo \t tag \t id \t epoch ────────────────────────────────
# 排序键用镜像真实创建时间（epoch）。`docker images --format` 的 CreatedAt
# 是 "2026-11-26 21:05:28 +0800" 形式，两种 date 实现分别兼容 macOS/GNU。
CAND="$TMPDIR_P/cand"
: > "$CAND"

TAB="$(printf '\t')"

docker images --format "{{.Repository}}${TAB}{{.Tag}}${TAB}{{.ID}}${TAB}{{.CreatedAt}}" 2>/dev/null \
| while IFS="$TAB" read -r repo tag id created; do
    [ "$tag" = "<none>" ] && continue
    # 只按 Repository 字段匹配（整行 grep 会被后面的 \t 干扰锚点）
    printf '%s\n' "$repo" | grep -qE "$PATTERNS" || continue
    # CreatedAt 形如 "2026-11-26 21:05:28 +0800 HKT" —— 去掉尾部时区缩写，
    # 否则 busybox/coreutils 的 date -d 会因 "HKT" 报 invalid date
    clean=$(printf '%s' "$created" | sed -E 's/[[:space:]]+[A-Z]{2,5}$//')
    epoch=$(date -d "$clean" +%s 2>/dev/null \
         || date -j -f "%Y-%m-%d %H:%M:%S %z" "$clean" +%s 2>/dev/null \
         || echo 0)
    printf '%s\t%s\t%s\t%s\n' "$repo" "$tag" "$id" "$epoch"
done > "$CAND"

if [ ! -s "$CAND" ]; then
    ok "没有匹配本项目命名规则的镜像，无需清理"
    exit 0
fi

# repo 分组 + 组内按 epoch 倒序（新→旧）
SORTED="$TMPDIR_P/sorted"
# 排序：repo 升序 → epoch 降序 → tag 降序
# 加 tag 做次级键是因为同一次 build 的多个 tag（手工 docker tag）CreatedAt
# 完全相同，只按 epoch 排会得到不稳定的顺序，可能误删正式 tag。
sort -t"$TAB" -k1,1 -k4,4r -k2,2r "$CAND" > "$SORTED"

# ── 逐条判断 ────────────────────────────────────────────────────────────
REMOVED=0; SKIPPED_IN_USE=0; KEPT=0
CUR_REPO=""; N=0
KEEP_FILE="$TMPDIR_P/keep"
: > "$KEEP_FILE"

while IFS="$TAB" read -r repo tag id epoch; do
    [ -z "${repo:-}" ] && continue

    # 换组则重置计数
    if [ "$repo" != "$CUR_REPO" ]; then
        CUR_REPO="$repo"; N=0
    fi
    N=$((N + 1))

    # 规则 3：最近 KEEP 个保留
    if [ "$N" -le "$KEEP" ]; then
        KEPT=$((KEPT + 1))
        printf '%s\t%s\t%s\n' "$repo" "$tag" "$id" >> "$KEEP_FILE"
        continue
    fi

    # 规则 2：在用的一律跳过（即使超出 KEEP）
    if is_in_use "$id"; then
        warn "跳过在用镜像 $repo:$tag ($id)"
        SKIPPED_IN_USE=$((SKIPPED_IN_USE + 1))
        continue
    fi

    # 同一镜像 ID 可能挂了多个 tag（手工 tag / 重复构建）。若这个 ID
    # 已有被保留的 tag，说明本条只是别名，删掉不损失任何层。
    if grep -q "	$id$" "$KEEP_FILE" 2>/dev/null; then
        note="（别名，同 ID 已有保留 tag）"
    else
        note=""
    fi

    if [ "$DRY_RUN" = "1" ]; then
        info "[dry-run] 将删除 $repo:$tag ($id)$note"
    else
        if docker rmi "$repo:$tag" >/dev/null 2>&1; then
            info "已删除 $repo:$tag$note"
        else
            warn "删除失败 $repo:$tag（可能与他 tag 共享层，交给悬空清理）"
        fi
    fi
    REMOVED=$((REMOVED + 1))
done < "$SORTED"

# ── 规则 4：悬空层 + 构建缓存 ───────────────────────────────────────────
if [ "$DRY_RUN" != "1" ]; then
    DANGLING=$(docker image prune -f 2>&1 | grep -oE 'Total reclaimed space: .*' || true)
    [ -n "$DANGLING" ] && ok "悬空层: $DANGLING"
    docker builder prune -f >/dev/null 2>&1 || true
fi

printf "\n"
ok "清理完成：删除 $REMOVED 个，保留 $KEPT 个，跳过在用 $SKIPPED_IN_USE 个（每个镜像保留最近 $KEEP 个版本）"
if [ "$DRY_RUN" = "1" ]; then
    warn "这是 dry-run，未实际删除。去掉 DRY_RUN=1 才会生效"
fi
