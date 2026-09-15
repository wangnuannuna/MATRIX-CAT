# -*- coding: utf-8 -*-
"""
离线单测：不开浏览器、不联网、无副作用，验纯逻辑(caption 拼装/可见性门控/submit 体/账号抽取/序列化)。

跑法(二选一)：
  python tests/test_offline.py          # 直接跑，打印每项 PASS/FAIL，全过退出码 0
  pytest tests/test_offline.py -q       # 有 pytest 的话也能被收集(函数名 test_*)
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))))  # 项目根，让 from config import settings 找得到
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding='utf-8')
    except Exception:
        pass

from kuaishou import config, note, protocol  # noqa: E402


def test_caption_inline_topics():
    """标题+正文换行拼，话题以 #tag 内联，最多 3 个。"""
    cap = note.build_caption('标题', '这是正文', ['探店', '美食', '日常', '第四个应被截掉'])
    assert cap.startswith('标题\n这是正文')
    assert cap.count('#') == 3 and '#第四个应被截掉' not in cap
    # 标题与正文相同时不重复
    assert note.build_caption('只有标题', '只有标题', None) == '只有标题'


def test_visibility_gating():
    """public 用最佳猜测放行；private/friends 未校准必须 fail-loud。"""
    assert note.resolve_photo_status('public') == note.CALIBRATE_VIS_PUBLIC
    for v in ('private', 'friends'):
        try:
            note.resolve_photo_status(v)
            assert False, f'{v} 未被拦截'
        except note.VisibilityCalibrationError:
            pass


def test_video_submit_structure():
    """视频 submit 体字段名 + 真值来自回执；缺 fileId/coverKey 必须报错。"""
    body = note.build_video_submit({
        'title': 't', 'desc': 'd', 'topics': ['话题'], 'visibility': 'public',
        'file_id': 'F1', 'cover_key': 'C1', 'media_id': 'M1'})
    assert body['fileId'] == 'F1' and body['coverKey'] == 'C1' and body['mediaId'] == 'M1'
    assert body['photoStatus'] == note.CALIBRATE_VIS_PUBLIC
    assert '#话题' in body['caption']
    for miss in ({'cover_key': 'C1'}, {'file_id': 'F1'}):
        try:
            note.build_video_submit(miss)
            assert False, '缺回执真值未拦截'
        except ValueError:
            pass


def test_atlas_submit_structure():
    """图文 submit 体：图片 key 数组来自回执；缺则报错。"""
    body = note.build_atlas_submit({
        'title': 't', 'visibility': 'public',
        'images': [{'key': 'K1'}, {'id': 'K2'}, {'file_id': 'K3'}]})
    assert body['atlasKeys'] == ['K1', 'K2', 'K3']
    try:
        note.build_atlas_submit({'images': [{'width': 1}]})
        assert False, '缺图片 key 未拦截'
    except ValueError:
        pass


def test_atlas_gate_default_off():
    """图文协议默认未校准(除非 env 显式开)，门控应关闭。"""
    if (os.getenv('KS_ATLAS_CALIBRATED', '') or '').lower() in ('1', 'true', 'yes'):
        return  # 环境显式开了就跳过这条(别误报)
    assert config.atlas_calibrated() is False


def test_serialize_compact():
    """serialize 出的串必须紧凑(无空格) —— 若走页内加签，签名/发送同串的前提。"""
    s = note.serialize(note.build_video_submit(
        {'file_id': 'F', 'cover_key': 'C', 'visibility': 'public'}))
    assert ', ' not in s and '": ' not in s, '序列化不该有空格'


def test_account_identity():
    """storage_state → cookie/api_ph/api_st；缺 api_ph/api_st 必须 fail-fast。"""
    ss = {'cookies': [
        {'name': config.CK_LOGIN_PH, 'value': 'PH', 'domain': '.kuaishou.com'},
        {'name': config.CK_LOGIN_ST, 'value': 'ST', 'domain': '.kuaishou.com'},
        {'name': config.CK_USERID, 'value': '123', 'domain': '.kuaishou.com'}]}
    ident = protocol.extract_identity(ss)
    assert ident['api_ph'] == 'PH' and ident['api_st'] == 'ST' and ident['user_id'] == '123'
    protocol.assert_account_ready(ss)  # 齐全，不报错
    # 缺 api_ph → fail-fast
    try:
        protocol.assert_account_ready({'cookies': [
            {'name': config.CK_LOGIN_ST, 'value': 'ST', 'domain': '.kuaishou.com'}]})
        assert False, '缺 api_ph 未 fail-fast'
    except protocol.AccountContractError:
        pass


def test_sig3_diagnostic_skips_cooldown_codes():
    """风控码(403/429/412)不该被 _sig3_diagnostic 误判成'签名未就绪'——那条归冷却处理，否则跳过冷却撞封。"""
    from kuaishou.protocol import ApiResp, _sig3_diagnostic, SigningNotReady
    _sig3_diagnostic(ApiResp(403, None, 'Forbidden'), 'x')   # 403 是风控码：不抛即通过
    _sig3_diagnostic(ApiResp(429, None, 'too many token'), 'x')
    try:                                                     # 400+sig 字样：非风控码，应诊断为缺 sig3
        _sig3_diagnostic(ApiResp(400, None, 'invalid __NS_sig3'), 'x')
        assert False, '缺 sig3 未诊断'
    except SigningNotReady:
        pass
    try:                                                     # 页内 fetch 异常 → PublishError
        _sig3_diagnostic(ApiResp(protocol.FETCH_ERROR, None, 'fetch_error'), 'x')
        assert False
    except protocol.PublishError:
        pass


def test_uploader_ok_gate():
    """网关 _ok:200+result≠1 / error* 判失败；200+result==1 或非 JSON 放行；非 200 判失败。"""
    class R:
        def __init__(self, sc, body, js=None):
            self.status_code, self.text, self._js = sc, body, js

        def json(self):
            if self._js is None:
                raise ValueError('no json')
            return self._js

    protocol.KsUploader._ok(R(200, 'ok'), 'x')               # 非 JSON 放行
    protocol.KsUploader._ok(R(200, '', {'result': 1}), 'x')  # result==1 放行
    for bad in ({'result': 0}, {'error': 'boom'}, {'error_code': 5}):
        try:
            protocol.KsUploader._ok(R(200, '', bad), 'x')
            assert False, f'{bad} 未拦'
        except RuntimeError:
            pass
    try:
        protocol.KsUploader._ok(R(500, 'err'), 'x')
        assert False, '非 200 未拦'
    except RuntimeError:
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
