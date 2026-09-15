# -*- coding: utf-8 -*-
"""
离线单测：不开浏览器、不联网、无副作用，验纯逻辑(note 体结构/COS 解析/账号抽取/可见性/序列化)。

跑法(二选一)：
  python tests/test_offline.py          # 直接跑，打印每项 PASS/FAIL，全过退出码 0
  pytest tests/test_offline.py -q       # 有 pytest 的话也能被收集(函数名 test_*)
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding='utf-8')
    except Exception:
        pass

from xhs import note, protocol  # noqa: E402


def test_image_note_structure():
    """图文 note 体键序/可见性/图片元素与真机校准一致。"""
    n = note.build_image_note({
        'title': '标题', 'desc': '正文#测试', 'topics': ['美食', '探店'],
        'visibility': 'self_only',
        'images': [{'file_id': 'F1', 'width': 1080, 'height': 1440}]})
    assert list(n['common'].keys()) == ['type', 'note_id', 'source', 'title', 'desc', 'ats',
                                        'hash_tag', 'business_binds', 'privacy_info',
                                        'goods_info', 'biz_relations', 'capa_trace_info']
    assert n['common']['privacy_info'] == {'op_type': 1, 'type': 1, 'user_ids': []}  # self_only=1
    assert n['video_info'] is None
    img = n['image_info']['images'][0]
    assert list(img.keys()) == ['file_id', 'width', 'height', 'metadata', 'stickers', 'extra_info_json']
    assert img['stickers'] == {'version': 2, 'floating': []}  # 真机复数键名 + floating


def test_video_note_structure():
    """视频 note 体 + video_info 键序/封面 neptune stickers 与真机一致。"""
    v = note.build_video_note({
        'title': 'v', 'video': {'file_id': 'V1', 'width': 720, 'height': 1280, 'duration': 1.6},
        'cover': {'file_id': 'C1', 'width': 720, 'height': 1280}, 'visibility': 'public'})
    assert v['common']['type'] == 'video' and v['image_info'] is None
    vi = v['video_info']
    assert vi['file_id'] == 'V1' and vi['video_preview_type'] == 'full_vertical_screen'
    assert vi['cover']['stickers'] == {'version': 2, 'neptune': []}  # 视频封面用 neptune 不是 floating
    # 真机结论：视频与图文的 business_binds 逐字节一致
    i = note.build_image_note({'images': [{'file_id': 'X'}], 'visibility': 'public'})
    assert i['common']['business_binds'] == v['common']['business_binds']


def test_visibility_gating():
    """public/self_only 正常；mutual_friends 未校准必须 fail-loud。"""
    assert note._resolve_visibility(None, 'public') == 0
    assert note._resolve_visibility(None, 'self_only') == 1
    try:
        note.build_image_note({'images': [{'file_id': 'X'}], 'visibility': 'mutual_friends'})
        assert False, 'mutual_friends 未被拦截'
    except note.VisibilityCalibrationError:
        pass
    # protocol 层把它转成 PublishError(给 CLI 干净报错)
    try:
        protocol.precheck_visibility('mutual_friends')
        assert False
    except protocol.PublishError:
        pass


def test_fileid_failloud():
    """缺 file_id 必须报错，不编造。"""
    try:
        note.build_image_note({'images': [{'width': 1}]})
        assert False, '缺 file_id 未拦截'
    except ValueError:
        pass


def test_serialize_compact():
    """serialize 出的串必须紧凑(无空格) —— 签名/发送同串的前提。"""
    s = note.serialize(note.build_image_note({'images': [{'file_id': 'X'}], 'visibility': 'public'}))
    assert ', ' not in s and '": ' not in s, '序列化不该有空格'
    assert s.startswith('{"common":{')


def test_cos_permit_parse():
    """permit 解析 camelCase / snake_case 双兜底。"""
    one = protocol.parse_permit({'data': {'uploadTempPermits': [
        {'fileIds': ['F'], 'token': 'T', 'uploadAddr': 'https://h/'}]}})
    assert one == {'file_id': 'F', 'token': 'T', 'upload_host': 'h'}


def test_account_identity():
    """storage_state → cookie/a1/b1；缺 a1 必须 fail-fast。"""
    ss = {'cookies': [{'name': 'a1', 'value': 'AAA', 'domain': '.xiaohongshu.com'}],
          'origins': [{'origin': 'https://www.xiaohongshu.com',
                       'localStorage': [{'name': 'b1', 'value': 'BBB'}]}]}
    ident = protocol.extract_identity(ss)
    assert ident == {'cookies': {'a1': 'AAA'}, 'a1': 'AAA', 'b1': 'BBB'}
    protocol.assert_account_ready(ss)  # 齐全，不报错
    try:
        protocol.assert_account_ready({'cookies': [], 'origins': []})
        assert False, '空账号态未 fail-fast'
    except protocol.AccountContractError:
        pass


def main():
    tests = [v for k, v in sorted(globals().items()) if k.startswith('test_') and callable(v)]
    failed = 0
    for t in tests:
        try:
            t()
            print(f'[PASS] {t.__name__}')
        except Exception as e:
            failed += 1
            print(f'[FAIL] {t.__name__}: {type(e).__name__}: {e}')
    print(f'\n{len(tests) - failed}/{len(tests)} 通过')
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main())
