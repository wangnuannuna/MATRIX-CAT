# -*- coding: utf-8 -*-
"""
离线单测:不开浏览器、不联网、无副作用,验纯逻辑(wbi 自签/身份抽取/投稿体/动态体/序列化)。

跑法(二选一):
  python tests/test_offline.py          # 直接跑,打印每项 PASS/FAIL,全过退出码 0
  pytest tests/test_offline.py -q
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))))  # 项目根,让 from config import settings 找得到
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding='utf-8')
    except Exception:
        pass

from bilibili import config, note, protocol, signer  # noqa: E402


def test_wbi_mixin_key():
    """mixin_key = 按重排表取前 32 位;长度 32,确定性,表内索引都在 0..63。"""
    assert max(signer.MIXIN_KEY_ENC_TAB) < 64 and len(signer.MIXIN_KEY_ENC_TAB) == 64
    orig = ''.join(f'{i % 16:x}' for i in range(64))   # 64 位可辨识串
    mk = signer.get_mixin_key(orig)
    assert len(mk) == 32
    assert signer.get_mixin_key(orig) == mk            # 确定性
    # 逐位对齐重排表定义
    assert mk == ''.join(orig[i] for i in signer.MIXIN_KEY_ENC_TAB)[:32]


def test_wbi_sign_deterministic():
    """sign_wbi 补 wts+w_rid;w_rid 是 32 位 hex,固定输入固定输出,不改原 params。"""
    img = 'a' * 32
    sub = 'b' * 32
    params = {'foo': 'bar', 'mid': 123}
    out1 = signer.sign_wbi(params, img, sub, wts=1700000000)
    out2 = signer.sign_wbi(params, img, sub, wts=1700000000)
    assert out1['wts'] == 1700000000
    assert len(out1['w_rid']) == 32 and all(c in '0123456789abcdef' for c in out1['w_rid'])
    assert out1['w_rid'] == out2['w_rid']              # 确定性
    assert 'w_rid' not in params                       # 不污染原 dict
    # 值里的 !'()* 会被过滤 → 影响签名,证明清洗生效
    assert signer.sign_wbi({'k': "a!b'c"}, img, sub, wts=1)['w_rid'] == \
        signer.sign_wbi({'k': 'abc'}, img, sub, wts=1)['w_rid']


def test_wbi_keys_from_nav():
    """从 nav 抽 img/sub key;缺 wbi_img 必须 raise。"""
    nav = {'code': 0, 'data': {'wbi_img': {
        'img_url': 'https://i0.hdslb.com/bfs/wbi/7cd084941338484aae1ad9425b84077c.png',
        'sub_url': 'https://i0.hdslb.com/bfs/wbi/4932caff0ff746eab6f01bf08b70ac45.png'}}}
    img, sub = signer.wbi_keys_from_nav(nav)
    assert img == '7cd084941338484aae1ad9425b84077c'
    assert sub == '4932caff0ff746eab6f01bf08b70ac45'
    try:
        signer.wbi_keys_from_nav({'data': {}})
        assert False, '缺 wbi_img 未拦'
    except signer.WbiKeyError:
        pass


def test_account_identity():
    """storage_state → SESSDATA/bili_jct/uid;缺 SESSDATA 或 bili_jct 必须 fail-fast。"""
    ss = {'cookies': [
        {'name': config.CK_SESSDATA, 'value': 'SD', 'domain': '.bilibili.com'},
        {'name': config.CK_CSRF, 'value': 'JCT', 'domain': '.bilibili.com'},
        {'name': config.CK_UID, 'value': '42', 'domain': '.bilibili.com'}]}
    ident = protocol.extract_identity(ss)
    assert ident['sessdata'] == 'SD' and ident['csrf'] == 'JCT' and ident['uid'] == '42'
    protocol.assert_account_ready(ss)  # 齐全,不报错
    for miss in ([{'name': config.CK_CSRF, 'value': 'JCT', 'domain': '.bilibili.com'}],   # 缺 SESSDATA
                 [{'name': config.CK_SESSDATA, 'value': 'SD', 'domain': '.bilibili.com'}]):  # 缺 bili_jct
        try:
            protocol.assert_account_ready({'cookies': miss})
            assert False, '缺关键 cookie 未 fail-fast'
        except protocol.AccountContractError:
            pass


def test_video_add_structure():
    """add/v3 体:真值来自回执(缺 filename/cover 报错);字段名/copyright/tag/tid 正确。"""
    body = note.build_video_add({
        'title': '标题', 'desc': '正文', 'topics': ['科技', '教程'], 'visibility': 'public',
        'filename': 'n230101abc', 'cid': 987654, 'cover': '//i0.hdslb.com/bfs/archive/x.jpg',
        'tid': 122})
    assert body['videos'][0]['filename'] == 'n230101abc' and body['videos'][0]['cid'] == 987654
    assert body['cover'].endswith('x.jpg') and body['tid'] == 122
    assert body['tag'] == '科技,教程' and body['copyright'] == 1 and body['no_reprint'] == 1
    # 转载:给 source → copyright 2、no_reprint 0
    b2 = note.build_video_add({'filename': 'f', 'cover': 'c', 'source': 'http://x', 'topics': ['a']})
    assert b2['copyright'] == 2 and b2['source'] == 'http://x' and b2['no_reprint'] == 0
    # 缺 filename / cover 必须报错
    for miss in ({'cover': 'c'}, {'filename': 'f'}):
        try:
            note.build_video_add(miss)
            assert False, '缺回执真值未拦'
        except ValueError:
            pass


def test_video_visibility_gate():
    """B站 web 投稿只有公开;private/friends 必须 fail-loud(别误公开)。"""
    for v in ('private', 'friends', 'self_only'):
        try:
            note.build_video_add({'filename': 'f', 'cover': 'c', 'visibility': v, 'topics': ['a']})
            assert False, f'{v} 未被拦'
        except note.VisibilityUnsupportedError:
            pass


def test_tag_fallback_and_tid_default():
    """无话题 → tag 兜底 '日常'(B站 tag 必填);无 tid → 用占位默认。"""
    body = note.build_video_add({'filename': 'f', 'cover': 'c'})
    assert body['tag'] == '日常'
    assert body['tid'] == config.DEFAULT_TID


def test_dynamic_draw_structure():
    """图文动态体:pics 来自回执(缺 img_src 报错);话题内联 #tag#;pictures 序列化成串。"""
    form = note.build_dynamic_draw({
        'title': '标题', 'desc': '正文', 'topics': ['日常'],
        'pics': [{'img_src': '//i0.hdslb.com/a.jpg', 'img_width': 100, 'img_height': 80, 'img_size': 12}]})
    assert form['biz'] == '3' and isinstance(form['pictures'], str)
    assert 'i0.hdslb.com/a.jpg' in form['pictures']
    assert '#日常#' in form['content']
    try:
        note.build_dynamic_draw({'pics': [{'img_width': 1}]})
        assert False, '缺 img_src 未拦'
    except ValueError:
        pass


def test_dyn_gate_default_off():
    """图文动态默认未校准(除非 env 显式开),门控应关闭。"""
    if (os.getenv('BILI_DYN_CALIBRATED', '') or '').lower() in ('1', 'true', 'yes'):
        return
    assert config.dynamic_calibrated() is False


def test_serialize_compact():
    """serialize 出的串必须紧凑(无空格)。"""
    s = note.serialize({'a': 1, 'b': [1, 2]})
    assert ', ' not in s and '": ' not in s, '序列化不该有空格'


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
