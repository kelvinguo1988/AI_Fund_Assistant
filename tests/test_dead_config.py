"""Q13 死配置与口径不一致清扫的回归防线（第二批 2D）

覆盖五个子项，每一项都对应一个"看起来能配/看起来对，其实不生效"的坑：
1. signal_rules 死配置 —— 截面/内嵌规则因子根本不读它，前端改了没反应；
2. MACD 文档写"放量"，实现里没有；`volume_history` 无人消费 → 字段已删；
3. 同一个"分位"两处定义不同（严格 < vs 含等号 <=）→ 统一 helper；
4. 两套估值分区（仪表盘 30/70 vs 策略极端 0.15/0.85）→ 保留但改名；
5. `size_stability` 量纲（份额 vs 元）与 `FundData.pb` 误标 → 换算/删字段。
"""

import inspect
import json
from pathlib import Path

import pytest

from backend.database import _build_factor_seeds, _clear_inert_signal_rules
from backend.data_sources.base import FundData
from backend.engines.factor_engine import (
    FACTOR_CALCULATORS,
    SIGNAL_RULES_INERT_FACTORS,
    FactorEngine,
    calculate_macd_signal,
    calculate_size_stability,
    signal_rules_effective,
)
from backend.utils.stats import percentile_rank_inclusive

REPO = Path(__file__).resolve().parents[1]

# 哨兵规则：若真被读取，得分必然变成 0.77；否则保持计算函数自己的输出
CANARY_RULES = [{"condition": ">= -1e9", "score": 0.77}]


def _src(path: str) -> str:
    return (REPO / path).read_text(encoding="utf-8")


# ═══════════════════════════════════════════════════════════════════
# 1. signal_rules 死配置
# ═══════════════════════════════════════════════════════════════════

class TestInertRulesListIsTruthful:
    def test_list_matches_calculator_source(self):
        """清单必须与"计算函数是否调用 rules_from_params"严格一致（防止以后漂移）"""
        for code, fn in FACTOR_CALCULATORS.items():
            uses_rules = "rules_from_params" in inspect.getsource(fn)
            assert signal_rules_effective(code) == uses_rules, (
                f"{code}: 清单说 rules {'生效' if signal_rules_effective(code) else '不生效'}，"
                f"源码{'调用' if uses_rules else '未调用'}了 rules_from_params"
            )

    def test_seeds_store_no_rules_for_inert_factors(self):
        for cfg in _build_factor_seeds():
            rules = json.loads(cfg["signal_rules"])
            if not signal_rules_effective(cfg["code"]):
                assert rules == [], f"{cfg['code']} 是死配置因子，种子里不该留规则"

    def test_seeds_keep_rules_for_live_factors(self):
        """反向保护：真配置（回撤修复度 / 市场环境 3 因子）不能被顺手清空"""
        live = {c["code"]: json.loads(c["signal_rules"]) for c in _build_factor_seeds()
                if signal_rules_effective(c["code"])}
        assert set(live) >= {"drawdown_recovery", "market_valuation",
                             "market_sentiment", "market_fund_flow"}
        assert all(rules for rules in live.values())

    def test_calculate_all_ignores_rules_only_for_inert(self):
        from test_engines import make_fund_data

        fd = make_fund_data(close_history_len=300)
        engine = FactorEngine()
        rows = [
            {"code": code, "name": code, "direction": "positive", "weight": 1.0,
             "params": json.dumps({"window": 60, "short_window": 20, "mid_window": 60}),
             "signal_rules": json.dumps(CANARY_RULES), "normalization": "none"}
            for code in ("short_momentum", "drawdown_recovery")
        ]
        out = {r.factor_code: r for r in engine.calculate_all(fd, rows)}

        # 死配置：规则被忽略，得分仍是 raw 动量
        assert out["short_momentum"].score != pytest.approx(0.77)
        assert out["short_momentum"].score == pytest.approx(out["short_momentum"].raw_value, abs=1e-6)
        # 真配置：同一条规则确实改写了得分
        assert out["drawdown_recovery"].score == pytest.approx(0.77)

    @pytest.mark.asyncio
    async def test_startup_migration_clears_inert_and_keeps_live(self, db_session):
        from backend.models.factor import Factor

        def row(code):
            return Factor(
                name=code, code=code, weight=1.0, direction="positive",
                normalization="cross_sectional_zscore" if code == "short_momentum" else "none",
                status="active", sort_order=1,
                signal_rules=json.dumps(CANARY_RULES),
            )

        inert, live = row("short_momentum"), row("drawdown_recovery")
        db_session.add_all([inert, live])
        await db_session.commit()

        assert await _clear_inert_signal_rules(db_session) == 1
        await db_session.commit()
        assert json.loads(inert.signal_rules) == []
        assert json.loads(live.signal_rules) == CANARY_RULES

        # 幂等：第二次跑不再改动任何东西
        assert await _clear_inert_signal_rules(db_session) == 0

    def test_api_exposes_effectiveness_flag(self):
        assert "signal_rules_effective: bool" in _src("backend/schemas/factor.py")
        assert "out.signal_rules_effective = signal_rules_effective(f.code)" in \
            _src("backend/routers/factor.py")

    def test_frontend_disables_editing_for_inert(self):
        page = _src("frontend/src/pages/FactorManagement.tsx")
        assert "signal_rules_effective === false" in page
        assert "规则不适用" in page
        assert "signal_rules_effective?: boolean" in _src("frontend/src/types/index.ts")


# ═══════════════════════════════════════════════════════════════════
# 2. MACD 文档 / volume_history 死字段
# ═══════════════════════════════════════════════════════════════════

class TestMacdDocAndDeadFields:
    def test_macd_doc_no_longer_lists_volume_as_a_rule(self):
        """规则段必须与实现一致；"放量"只允许出现在 Q13 的踩坑说明里"""
        doc = inspect.getsource(calculate_macd_signal)
        spec, _, note = doc.partition("Q13")
        assert "放量" not in spec and "缩量" not in spec
        assert "DIF>DEA" in spec and "柱收敛" in spec
        assert "从来没有量能项" in note

    def test_macd_scoring_rules_match_doc(self):
        """金叉且柱放大 → 1.0；金叉但柱收缩 → 0.5；死叉且柱走弱 → -1.0"""
        rising = [4.0 + 0.01 * i + 0.002 * i * i for i in range(80)]
        fd = FundData(code="510300", close_history=rising)
        assert calculate_macd_signal(fd).score in (1.0, 0.5)
        falling = [4.0 - 0.01 * i - 0.001 * i * i for i in range(80)]
        assert calculate_macd_signal(FundData(code="510300", close_history=falling)).score == -1.0

    def test_funddata_has_no_pb_or_volume_history(self):
        fields = set(FundData.__dataclass_fields__)
        assert "pb" not in fields
        assert "volume_history" not in fields
        assert "pe" in fields

    def test_no_adapter_assigns_the_deleted_fields(self):
        for rel in ("backend/data_sources/akshare_adapter.py",
                    "backend/data_sources/joinquant_adapter.py"):
            src = _src(rel)
            assert ".pb =" not in src and "volume_history" not in src


# ═══════════════════════════════════════════════════════════════════
# 3. 分位口径统一
# ═══════════════════════════════════════════════════════════════════

class TestPercentileSingleDefinition:
    def test_inclusive_semantics(self):
        assert percentile_rank_inclusive([1, 2, 3], 2) == pytest.approx(2 / 3)
        # 旧的严格 < 口径会给 1/3 —— 两处差一个点就是"同一个指数两个分位"的成因
        assert percentile_rank_inclusive([1, 2, 3], 2) != pytest.approx(1 / 3)
        assert percentile_rank_inclusive([2, 2, 2], 2) == pytest.approx(1.0)
        assert percentile_rank_inclusive([1.0, None, 3.0], 3.0) == pytest.approx(1.0)
        assert percentile_rank_inclusive([], 1.0) is None

    def test_both_valuation_paths_call_the_helper(self):
        ivs = _src("backend/services/index_valuation_service.py")
        mrs = _src("backend/services/market_regime_service.py")
        for rel, src in (("index_valuation", ivs), ("market_regime", mrs)):
            assert "percentile_rank_inclusive" in src, rel
        assert "(pe_series < current_pe)" not in ivs
        assert "if p <= current_pe" not in mrs

    def test_two_partitions_are_named_apart(self):
        ivs = _src("backend/services/index_valuation_service.py")
        assert "DASHBOARD_PE_LOW_PERCENTILE" in ivs and "DASHBOARD_PE_HIGH_PERCENTILE" in ivs
        assert "LOW_THRESHOLD" not in ivs and "HIGH_THRESHOLD" not in ivs
        qf = _src("backend/engines/quality_filter.py")
        assert "策略极端低/高估" in qf and "仪表盘" in qf
        # 数值仍然各是各的（保留差异，只是不再混淆）
        assert '"extreme_high_valuation_pct": 0.85' in qf
        assert '"extreme_low_valuation_pct": 0.15' in qf
        assert "DASHBOARD_PE_LOW_PERCENTILE = 30.0" in ivs
        assert "DASHBOARD_PE_HIGH_PERCENTILE = 70.0" in ivs

    def test_dashboard_zone_boundaries_use_dashboard_constants(self):
        from backend.services.index_valuation_service import pe_zone

        assert pe_zone(29.9) == "低估" and pe_zone(30.0) == "合理"
        assert pe_zone(70.0) == "合理" and pe_zone(70.1) == "高估"


# ═══════════════════════════════════════════════════════════════════
# 4. size_stability 量纲
# ═══════════════════════════════════════════════════════════════════

class TestSizeStabilityUnits:
    def _final(self, sizes):
        fd = FundData(code="159915", fund_size_history=list(sizes))
        return calculate_size_stability(fd)

    def test_bonus_bins_are_yuan_not_shares(self):
        yuan = self._final([3.0e8, 3.2e8, 3.1e8, 3.05e8])      # 3 亿元
        shares_as_yuan = self._final([3.0, 3.2, 3.1, 3.05])     # 把"份"当"元"
        assert yuan.score > shares_as_yuan.score                # 2e8~5e9 元档 +0.2
        assert yuan.raw_value == pytest.approx(5.2, abs=1e-6)

    def test_docstring_declares_yuan_and_disabled_status(self):
        doc = inspect.getsource(calculate_size_stability)
        assert "元" in doc and "未启用" in doc

    def test_adapter_converts_shares_to_yuan(self):
        src = _src("backend/data_sources/akshare_adapter.py")
        assert "份额 × 最新单位净值" in src or "份额 × 最新净值" in src


# ═══════════════════════════════════════════════════════════════════
# 5. Q14 工程闸门：CI 必须真的跑测试
# ═══════════════════════════════════════════════════════════════════

class TestCiActuallyGates:
    CI_PATH = REPO / ".github" / "workflows" / "ci.yml"

    def test_workflow_exists_and_runs_tests_and_build(self):
        assert self.CI_PATH.exists(), "Q14：只有镜像构建、没有测试闸门的仓等于没有 CI"
        text = self.CI_PATH.read_text(encoding="utf-8")
        runs = [ln.strip() for ln in text.splitlines() if ln.strip().startswith("run:")]
        assert any("pytest" in r for r in runs)
        assert any("npm run build" in r for r in runs)

    def test_toolchain_versions_match_dockerfiles(self):
        """CI 用 3.9/node20 与镜像一致，否则绿的是 CI 不是生产"""
        text = self.CI_PATH.read_text(encoding="utf-8")
        assert 'python-version: "3.9"' in text
        assert 'node-version: "20"' in text
        assert "backend/requirements.txt" in text
        assert "frontend/package-lock.json" in text

    def test_no_lint_gate_yet(self):
        """Q14b 未做 → 别在 CI 里偷偷塞一次性上千条的静态检查"""
        text = self.CI_PATH.read_text(encoding="utf-8")
        for ln in text.splitlines():
            if ln.strip().startswith("run:"):
                assert not any(t in ln for t in ("ruff", "mypy", "eslint", "tsc --"))

    def test_workflow_is_valid_yaml(self):
        yaml = pytest.importorskip("yaml")
        doc = yaml.safe_load(self.CI_PATH.read_text(encoding="utf-8"))
        assert set(doc["jobs"]) == {"backend-tests", "frontend-build"}
        for job in doc["jobs"].values():
            assert job["runs-on"] == "ubuntu-latest" and job["steps"]
        # PyYAML 按 YAML 1.1 把裸 `on:` 解析成布尔键 True，触发器挂在它下面
        triggers = doc.get(True, doc.get("on"))
        assert triggers["push"]["branches"] == ["main"]


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))
