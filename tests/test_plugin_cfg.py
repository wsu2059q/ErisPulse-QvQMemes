# -*- coding: utf-8 -*-
"""plugin_config 契约回归：QvQChat 实际返回 {模块名: {key: value}} 嵌套形。"""
from QvQMemes.Main import Main


def test_extract_values_nested_module_shape():
    remote = {"QvQMemes": {"auto_collect": False, "max_library": 300}}
    got = Main._extract_values(remote, module_name="QvQMemes")
    assert got == {"auto_collect": False, "max_library": 300}


def test_extract_values_flat_and_wrapper_shapes_still_work():
    assert Main._extract_values({"auto_collect": False}, module_name="QvQMemes") == {"auto_collect": False}
    assert Main._extract_values({"values": {"max_library": 9}}, module_name="QvQMemes") == {"max_library": 9}
    assert Main._extract_values({"Other": {"x": 1}}, module_name="QvQMemes") == {}
