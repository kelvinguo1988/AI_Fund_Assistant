"""市场温度计可用性与可观测性（2026-10-27）

线上问题：推送里有中证800 温度计，但首页 /dashboard 整块不显示。
排查后发现三个问题，都不是「功能没做」而是「做错了看不见」：

1. 接口返回 data:null 时前端整块消失，用户以为功能没了；
2. 取数失败只写 stdout 日志，不进 error_logs，页面/系统设置都查不到；
3. 连通性诊断里没有乐咕这一路，只能靠猜（上次 fund_etf_spot_em 同类问题
   也补过一次目标列表）。

本文件锁住后两点（前端占位由 tsc/build 保证）。
"""

import inspect
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


class TestFailureIsObservable:
    @staticmethod
    def _src() -> str:
        """读源文件：@classmethod 的 getsource 拿不到装饰器体，直接读文件更可靠"""
        from pathlib import Path
        import backend.services.market_temperature_service as m

        return (Path(m.__file__)).read_text(encoding="utf-8")

    def test_fetch_failure_logs_to_error_store(self):
        """取数失败必须落 error_logs，否则线上无从定位"""
        src = self._src()
        assert "log_source_failure" in src, "温度计失败未进错误日志"
        assert "temperature.leigu" in src, "错误日志模块名缺失"

    def test_failure_category_distinguishes_dependency(self):
        """依赖缺失(racer/import)与网络故障要分类，便于一眼定位"""
        src = self._src()
        assert "dependency" in src
        assert "network" in src

    def test_align_failure_also_logged(self):
        """PE/PB 对不齐（数据质量问题）也要上报，不能只 catch 网络"""
        src = self._src()
        assert "data_quality" in src, "对齐失败未分类上报"


class TestConnectivityTargets:
    def test_legulegu_in_targets(self):
        """乐咕是温度计/指数估值的唯一上游，必须在连通性诊断里"""
        from backend.services.connectivity_service import TARGETS

        names = {t["name"] for t in TARGETS}
        assert "legulegu.com" in names, "连通性诊断缺乐咕，故障时只能靠猜"

    def test_targets_are_well_formed(self):
        from backend.services.connectivity_service import TARGETS

        for t in TARGETS:
            assert t.get("name") and t.get("url", "").startswith("http")


class TestServiceContract:
    def test_returns_none_not_raise_on_failure(self):
        """失败必须返回 None 而不是抛异常：首页其他模块不能被温度计拖垮"""
        from backend.services.market_temperature_service import MarketTemperatureService

        src = inspect.getsource(MarketTemperatureService.get_temperature)
        # 所有失败分支都应 return cls._cache（可能是 None），不得 raise
        assert "raise" not in src

    def test_has_cooldown(self):
        """失败要冷却，避免每次刷新都打一遍已挂的上游"""
        from backend.services.market_temperature_service import MarketTemperatureService

        assert MarketTemperatureService._FAIL_COOLDOWN > 0
