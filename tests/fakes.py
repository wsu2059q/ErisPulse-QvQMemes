"""最小测试替身（参照 QvQStalker/tests/fakes.py 风格，独立精简、零框架依赖）。"""

from __future__ import annotations

import asyncio


class FakeStorage:
    def __init__(self):
        self._data = {}

    def get(self, key, default=None):
        return self._data.get(key, default)

    def set(self, key, value):
        self._data[key] = value

    def delete(self, key):
        self._data.pop(key, None)

    def keys(self):
        return list(self._data)


class FakeLogger:
    def __init__(self):
        self.records = []  # (level, message)

    def _log(self, level, *args, **kwargs):
        self.records.append((level, " ".join(str(a) for a in args)))

    def debug(self, *args, **kwargs):
        self._log("debug", *args)

    def info(self, *args, **kwargs):
        self._log("info", *args)

    def warning(self, *args, **kwargs):
        self._log("warning", *args)

    def error(self, *args, **kwargs):
        self._log("error", *args)

    def get_child(self, name):
        child = FakeLogger()
        # 子 logger 记录冒泡到父，便于断言
        child.records = self.records
        return child

    def of_level(self, level):
        return [msg for lv, msg in self.records if lv == level]


class FakeLifecycle:
    def __init__(self):
        self.registered = []    # (event, handler)
        self.unregistered = []  # (event, handler)

    def register(self, event, handler, *_, **__):
        self.registered.append((event, handler))

    def unregister(self, event, handler=None, *_, **__):
        self.unregistered.append((event, handler))

    async def emit(self, event, data=None, **kwargs):
        for evt, handler in list(self.registered):
            if evt != event:
                continue
            result = handler(data)
            if asyncio.iscoroutine(result):
                result = await result
        return data


class FakeModuleManager:
    """module.call 替身：handlers[(module, service)] = callable(**kwargs) | 常量。

    参数名对齐真实框架 ``call(self, module_name, method, ...)``——避免与服务
    参数里的 ``module=`` 关键字撞名。
    """

    def __init__(self, handlers=None):
        self.handlers = dict(handlers or {})
        self.calls = []  # (module, service, kwargs)

    async def call(self, module_name, method, *args, **kwargs):
        self.calls.append((module_name, method, kwargs))
        key = (module_name, method)
        if key in self.handlers:
            handler = self.handlers[key]
            if callable(handler):
                result = handler(**kwargs)
                if asyncio.iscoroutine(result):
                    result = await result
                return result
            return handler
        raise RuntimeError(f"module {module_name} service {method} not available")


class FakeSendChain:
    """SendDSL 链替身：记录 To/方法调用；发送方法返回 awaitable（对齐真实 DSL）。"""

    def __init__(self, log):
        self._log = log

    def To(self, target_type, target_id):
        self._log.append(("To", target_type, target_id))
        return self

    async def Image(self, ref):
        self._log.append(("Image", ref))
        return {"ok": True, "method": "Image", "ref": ref}

    async def Text(self, content=""):
        self._log.append(("Text", content))
        return {"ok": True, "method": "Text"}

    async def Markdown(self, content=""):
        self._log.append(("Markdown", content))
        return {"ok": True, "method": "Markdown"}


class FakeAdapter:
    def __init__(self, name="onebot11"):
        self.name = name
        self.send_log = []

    @property
    def Send(self):
        return FakeSendChain(self.send_log)


class FakeAdapterManager:
    def __init__(self, adapters=None):
        self.adapters = dict(adapters or {})

    def get(self, platform):
        return self.adapters.get(platform)

    def list_sends(self, platform):
        return ["Text", "Markdown", "Image"]

    def list_registered(self):
        return list(self.adapters)


class FakeSDK:
    def __init__(self, handlers=None, adapters=None):
        self.storage = FakeStorage()
        self.logger = FakeLogger()
        self.lifecycle = FakeLifecycle()
        self.module = FakeModuleManager(handlers)
        self.adapter = FakeAdapterManager(adapters)


def make_config(**overrides):
    from QvQMemes.Config import QvQMemesConfig

    fields = QvQMemesConfig.__dataclass_fields__
    bad = [k for k in overrides if k not in fields or k.startswith("_")]
    if bad:
        raise TypeError(f"unknown config keys: {bad}")
    return QvQMemesConfig(**overrides)


async def drain_tasks(collector):
    """等 collector 派发的所有后台任务（含级联 spawn 的打标签）跑完。"""
    for _ in range(20):
        if not collector.tasks:
            return
        await asyncio.gather(*list(collector.tasks), return_exceptions=True)
