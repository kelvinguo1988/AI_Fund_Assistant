"""镜像清理脚本的静态约束（2026-11-26）

这个脚本会执行 `docker rmi`，一旦逻辑写错就会删掉正在跑的镜像。
所以用测试锁住几条安全不变量，避免后续改动引入风险。

真实的删除行为在 NAS 上用 dry-run 验证过（造 test-v1/v2/v3 三个 tag，
确认能识别在用镜像并跳过、能保留最近 N 个、别名 tag 正确标注）。
"""

import re
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "prune_images.sh"
DEPLOY = Path(__file__).resolve().parent.parent / "deploy.sh"


@pytest.fixture(scope="module")
def src() -> str:
    return SCRIPT.read_text(encoding="utf-8")


class TestSafetyInvariants:
    def test_script_exists_and_executable(self):
        assert SCRIPT.exists()
        assert SCRIPT.stat().st_mode & 0o111, "必须可执行"

    def test_only_project_images_matched(self, src):
        """必须按仓库名过滤，绝不能对全部镜像动手"""
        assert "PATTERNS" in src
        assert "grep -qE \"$PATTERNS\"" in src, "应逐条按 repo 名匹配"
        # 默认规则只含本项目
        m = re.search(r'PATTERNS="\$\{PRUNE_PATTERNS:-([^}]+)\}"', src)
        assert m, "PATTERNS 应有默认值"
        assert "backend" in m.group(1) and "frontend" in m.group(1)

    def test_skips_images_in_use(self, src):
        """在用镜像（含已停止容器）必须跳过"""
        assert "is_in_use" in src
        assert "docker ps -aq" in src, "要含已停止容器，防止删掉待重启的镜像"

    def test_in_use_id_normalized_to_short(self, src):
        """docker inspect 给 sha256:xxx 全长，docker images 给 12 位短 ID

        两边格式不统一会永远匹配不上——2026-11-26 实测踩过这个坑，
        差点把正在跑的 backend 删掉。
        """
        assert '${full#sha256:}' in src, "要剥掉 sha256: 前缀"
        assert "cut -c1-12" in src, "要截成 12 位与 docker images 对齐"

    def test_keeps_n_versions(self, src):
        assert 'KEEP="${1:-2}"' in src, "默认保留 2 个版本便于回滚"
        assert 'if [ "$N" -le "$KEEP" ]' in src

    def test_dry_run_supported(self, src):
        """必须有 dry-run，否则没法安全试运行"""
        assert "DRY_RUN" in src
        assert "将删除" in src

    def test_never_removes_dangling_via_rmi(self, src):
        """<none> tag 交给 image prune，不用 rmi（rmi <none> 会失败）"""
        assert '[ "$tag" = "<none>" ] && continue' in src


class TestCompatibility:
    def test_posix_only_no_bash4_features(self, src):
        """macOS 自带 bash 3.2、NAS 也可能是旧 bash / busybox sh

        只检查**可执行代码行**——注释里说明"不要用 mapfile"是正常的。
        """
        code_lines = [
            ln for ln in src.splitlines()
            if not ln.lstrip().startswith("#")
        ]
        code = "\n".join(code_lines)
        for bad in ("mapfile", "declare -A", "readarray"):
            assert bad not in code, f"不能用 bash4+ 特性: {bad}"

    def test_date_handles_timezone_suffix(self, src):
        """docker 的 CreatedAt 带 'HKT' 之类后缀，date -d 会报 invalid date"""
        assert "sed -E" in src and "[A-Z]{2,5}" in src, "要先剥掉时区缩写"

    def test_handles_both_date_implementations(self, src):
        """GNU date -d 与 BSD date -j 都要试"""
        assert "date -d" in src
        assert "date -j" in src


class TestDeployIntegration:
    def test_deploy_calls_prune(self):
        d = DEPLOY.read_text(encoding="utf-8")
        assert "scripts/prune_images.sh" in d, "deploy.sh 应调用清理脚本"

    def test_prune_failure_does_not_break_deploy(self):
        """清理是收尾动作，失败不能把部署判为失败"""
        d = DEPLOY.read_text(encoding="utf-8")
        assert "|| warn" in d, "清理失败要 warn 而非 fail"
