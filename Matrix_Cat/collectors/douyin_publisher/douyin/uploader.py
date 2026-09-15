# -*- coding: utf-8 -*-
"""抖音素材上传:ByteDance VOD(视频)/ ImageX(图文·封面)。

回蚁小二 reptile 源码(见 [[project_yixiaoer_analysis]]),抖音上传分两个面:
  · 控制面(bytedanceapi.com 的 Apply/Commit)—— 走 **AWS4-HMAC-SHA256**(mhart/aws4 口径):
      region=cn-north-1;service=vod / imagex;STS 凭证来自
      GET creator.douyin.com/web/api/media/upload/auth/v5/ 响应里 auth(JSON 串)→
      {AccessKeyID, SecretAccessKey, SessionToken}。
      aws4 会签**除忽略集外的所有头**(host/x-amz-date/x-amz-security-token/content-type/
      x-amz-content-sha256/referer/origin…);忽略集={authorization,connection,x-amzn-trace-id,
      user-agent,expect,presigned-expires};body-hash 取 X-Amz-Content-Sha256 头或 sha256(body)。
  · 数据面(把字节 PUT 到 UploadHost)—— **不走 AWS4**,用 Apply 返回的
      StoreInfos[0].Auth 当 Authorization + Content-CRC32 + X-Storage-U。

链路:
  视频:ApplyUploadInner(GET vod) → PUT bytes 到 UploadHost → CommitUploadInner(POST vod) → vid
  图文:ApplyImageUpload(GET imagex) → PUT bytes → CommitImageUpload(POST imagex) → image uri
AWS4 核心用 shared.signing.aws4_authorization(对着 AWS 官方 SigV4 向量校过,见本文件 selftest)。
"""
import json
import logging
import os
import time
import zlib
from typing import Any, Dict, Optional

from . import config
from shared import signing
from shared.http import make_session, request_retry

logger = logging.getLogger(config.PLATFORM)

# mhart/aws4 的"永不签名"集(逐字对齐源码 var w)
_AWS4_IGNORE = {"authorization", "connection", "x-amzn-trace-id",
                "user-agent", "expect", "presigned-expires"}
_REGION = "cn-north-1"


class UploadError(Exception):
    pass


# ════════════════════════════════════════════════════════════════════════════
# AWS4(mhart/aws4 口径)
# ════════════════════════════════════════════════════════════════════════════
def _amz_now():
    amz = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    return amz, amz[:8]


def _rand_s() -> str:
    # 源码用 Math.random().toString(36).substring(2);这里等价一个随机短串
    return format(zlib.crc32(os.urandom(8)) & 0xFFFFFFFF, "x")


def _crc32_hex(data: bytes) -> str:
    """无符号 CRC32 → 8 位十六进制零填充(store 的 Content-CRC32 要这个)。
    注:源码 JS 用了 crc32(...).toString(16).replace('-','')(未 >>>0),对高位为 1 的值是错的;
    这里按标准无符号算,真机若报 CRC 不符再照 JS 复刻。"""
    return format(zlib.crc32(data) & 0xFFFFFFFF, "08x")


def _aws4_headers(method: str, host: str, uri: str, query: Optional[dict],
                  extra_headers: Optional[dict], body: bytes, creds: dict, service: str,
                  *, amz_date: Optional[str] = None) -> Dict[str, str]:
    """算一份带 Authorization 的请求头(要发出去的)。签名口径照 mhart/aws4。"""
    if amz_date:
        amz, datestamp = amz_date, amz_date[:8]
    else:
        amz, datestamp = _amz_now()

    # 参与签名的头(小写)
    hdr = {"host": host, "x-amz-date": amz}
    tok = creds.get("SessionToken") or creds.get("session_token")
    if tok:
        hdr["x-amz-security-token"] = tok
    for k, v in (extra_headers or {}).items():
        hdr[k.lower()] = v

    payload_hash = hdr.get("x-amz-content-sha256") or signing.sha256_hex(body or b"")
    signed = ";".join(sorted(k for k in hdr if k not in _AWS4_IGNORE))

    ak = creds.get("AccessKeyID") or creds.get("access_key")
    sk = creds.get("SecretAccessKey") or creds.get("secret_key")
    if not ak or not sk:
        raise UploadError("STS 凭证缺 AccessKeyID/SecretAccessKey —— upload/auth/v5 没拿到有效 auth")

    authorization, _sig = signing.aws4_authorization(
        method=method, uri=uri, query=query or {}, headers=hdr, payload_hash=payload_hash,
        access_key=ak, secret_key=sk, region=_REGION, service=service,
        amz_date=amz, datestamp=datestamp, signed_headers=signed)

    out = dict(hdr)                 # 发出去的头 = 已签的那些(值一致)
    out["Authorization"] = authorization
    out["User-Agent"] = config.DEFAULT_UA   # 不参与签名(在忽略集)
    return out


# ════════════════════════════════════════════════════════════════════════════
# STS 凭证:upload/auth/v5
# ════════════════════════════════════════════════════════════════════════════
def get_upload_sts(session, cookie_header: str) -> dict:
    """GET creator.douyin.com/web/api/media/upload/auth/v5/ → 解出 STS 凭证。

    响应里 auth 是个 JSON 串,parse 出 {AccessKeyID, SecretAccessKey, SessionToken}。
    真机响应层级可能是 data.auth / data.xxx.auth,这里做点容错。"""
    url = config.CREATOR_HOST + config.PATH_UPLOAD_AUTH
    r = request_retry(session, "GET", url, headers={
        "Cookie": cookie_header,
        "Accept": "application/json, text/plain, */*",
        "Referer": config.REFERER_PUBLISH,
        "User-Agent": config.DEFAULT_UA,
    })
    data = r.json()
    auth = _dig_auth(data)
    if auth is None:
        raise UploadError(f"upload/auth/v5 没解出 auth 凭证:{json.dumps(data, ensure_ascii=False)[:200]}")
    if isinstance(auth, str):
        auth = json.loads(auth)
    return auth


def _dig_auth(data: Any):
    """从响应里找那个含 AccessKeyID 的 auth(可能是 JSON 串或 dict,可能嵌一层)。"""
    if isinstance(data, str):
        try:
            data = json.loads(data)
        except ValueError:
            return None
    if isinstance(data, dict):
        if "auth" in data:
            return data["auth"]
        # 有的版本直接把凭证摊平
        if data.get("AccessKeyID") or data.get("access_key"):
            return data
        for v in data.values():
            got = _dig_auth(v)
            if got is not None:
                return got
    return None


# ════════════════════════════════════════════════════════════════════════════
# 控制面:Apply / Commit
# ════════════════════════════════════════════════════════════════════════════
def _check_meta(data: dict, what: str):
    err = (data.get("ResponseMetadata") or {}).get("Error") if isinstance(data, dict) else None
    if err and err.get("Code"):
        raise UploadError(f"{what} 失败: {err.get('Code')} {err.get('Message')}")


def apply_upload_inner(session, creds: dict, user_id, file_size: int) -> dict:
    """VOD ApplyUploadInner(GET vod.bytedanceapi.com)→ InnerUploadAddress。"""
    host = "vod.bytedanceapi.com"
    params = {
        "Action": "ApplyUploadInner", "Version": config.VOD_API_VERSION,
        "SpaceName": config.VOD_SPACE_NAME, "FileType": "video", "IsInner": 1,
        "FileSize": file_size, "app_id": config.VOD_APP_ID, "user_id": user_id, "s": _rand_s(),
    }
    headers = _aws4_headers("GET", host, "/", params, {"referer": config.REFERER_PUBLISH},
                            b"", creds, "vod")
    r = request_retry(session, "GET", config.VOD_HOST, params=params, headers=headers)
    data = r.json()
    _check_meta(data, "ApplyUploadInner")
    return data


def commit_upload_inner(session, creds: dict, user_id, session_key: str) -> dict:
    """VOD CommitUploadInner(POST vod)→ 拿 vid。"""
    host = "vod.bytedanceapi.com"
    params = {"Action": "CommitUploadInner", "Version": config.VOD_API_VERSION,
              "SpaceName": config.VOD_SPACE_NAME, "app_id": config.VOD_APP_ID, "user_id": user_id}
    body_obj = {"SessionKey": session_key,
                "Functions": [{"name": "GetMeta"}, {"name": "Snapshot", "input": {"SnapshotTime": 0}}]}
    body = json.dumps(body_obj, separators=(",", ":")).encode()
    extra = {"content-type": "application/json",
             "x-amz-content-sha256": signing.sha256_hex(body),
             "referer": config.REFERER_PUBLISH, "origin": config.ORIGIN}
    headers = _aws4_headers("POST", host, "/", params, extra, body, creds, "vod")
    r = request_retry(session, "POST", config.VOD_HOST, params=params, data=body, headers=headers)
    data = r.json()
    _check_meta(data, "CommitUploadInner")
    return data


def apply_image_upload(session, creds: dict, user_id, file_size: int, ext: str = "jpeg") -> dict:
    """ImageX ApplyImageUpload(GET imagex)→ InnerUploadAddress。"""
    host = "imagex.bytedanceapi.com"
    params = {"Action": "ApplyImageUpload", "Version": config.IMAGEX_API_VERSION,
              "ServiceId": config.IMAGEX_SERVICE_ID, "app_id": config.VOD_APP_ID,
              "user_id": user_id, "s": _rand_s()}
    headers = _aws4_headers("GET", host, "/", params,
                            {"referer": config.REFERER_PUBLISH, "origin": config.ORIGIN.rstrip("/")},
                            b"", creds, "imagex")
    r = request_retry(session, "GET", config.IMAGEX_HOST, params=params, headers=headers)
    data = r.json()
    _check_meta(data, "ApplyImageUpload")
    return data


def commit_image_upload(session, creds: dict, user_id, session_key: str) -> dict:
    """ImageX CommitImageUpload(POST imagex)→ 图片 uri。"""
    host = "imagex.bytedanceapi.com"
    params = {"Action": "CommitImageUpload", "Version": config.IMAGEX_API_VERSION,
              "ServiceId": config.IMAGEX_SERVICE_ID, "app_id": config.VOD_APP_ID, "user_id": user_id}
    body_obj = {"SessionKey": session_key}
    body = json.dumps(body_obj, separators=(",", ":")).encode()
    extra = {"content-type": "application/json",
             "x-amz-content-sha256": signing.sha256_hex(body),
             "referer": config.REFERER_PUBLISH, "origin": config.ORIGIN.rstrip("/")}
    headers = _aws4_headers("POST", host, "/", params, extra, body, creds, "imagex")
    r = request_retry(session, "POST", config.IMAGEX_HOST, params=params, data=body, headers=headers)
    data = r.json()
    _check_meta(data, "CommitImageUpload")
    return data


# ════════════════════════════════════════════════════════════════════════════
# 数据面:把字节 PUT 到 UploadHost(用 StoreInfos.Auth,不走 AWS4)
# ════════════════════════════════════════════════════════════════════════════
def _first_node(apply_resp: dict) -> dict:
    node = (((apply_resp.get("Result") or {}).get("InnerUploadAddress") or {})
            .get("UploadNodes") or [None])[0]
    if not node or not node.get("StoreInfos"):
        raise UploadError(f"Apply 响应没有可用 UploadNode/StoreInfos:"
                          f"{json.dumps(apply_resp, ensure_ascii=False)[:200]}")
    return node


def put_store_bytes(session, node: dict, user_id, data: bytes) -> dict:
    """单请求把整个文件 POST 到 UploadHost(小/中文件够用;大视频分片是 TODO)。"""
    store = node["StoreInfos"][0]
    url = f"https://{node['UploadHost']}/upload/v1/{store['StoreUri']}"
    headers = {
        "Content-CRC32": _crc32_hex(data),
        "Authorization": store["Auth"],
        "Content-Type": "application/octet-stream",
        "X-Storage-U": str(user_id),
        "User-Agent": config.DEFAULT_UA,
        "Referer": config.REFERER_PUBLISH,
        "Origin": config.ORIGIN.rstrip("/"),
    }
    r = request_retry(session, "POST", url, data=data, headers=headers)
    try:
        body = r.json()
    except Exception:
        body = {"raw": r.text[:200]}
    if isinstance(body, dict) and body.get("code") not in (None, 200, 2000):
        raise UploadError(f"字节上传失败 code={body.get('code')} msg={body.get('message')}")
    return {"store_uri": store["StoreUri"], "session_key": node.get("SessionKey"), "resp": body}


# ════════════════════════════════════════════════════════════════════════════
# 编排
# ════════════════════════════════════════════════════════════════════════════
def upload_video(cookie_header: str, user_id, path: str, *, proxy=None) -> dict:
    """视频三段式 → 返回 {vid, commit}。"""
    session = make_session(impersonate=config.CURL_IMPERSONATE, proxy=proxy,
                           timeout=config.UPLOAD_TIMEOUT)
    creds = get_upload_sts(session, cookie_header)
    with open(path, "rb") as f:
        data = f.read()
    apply_resp = apply_upload_inner(session, creds, user_id, len(data))
    node = _first_node(apply_resp)
    put = put_store_bytes(session, node, user_id, data)
    commit = commit_upload_inner(session, creds, user_id, put["session_key"])
    vid = _dig_first(commit, ("Vid", "vid"))
    logger.info(f"视频上传完成 vid={vid} size={len(data)}")
    return {"vid": vid, "commit": commit, "store_uri": put["store_uri"]}


def upload_image(cookie_header: str, user_id, path: str, *, proxy=None) -> dict:
    """图片三段式 → 返回 {uri, commit}。"""
    session = make_session(impersonate=config.CURL_IMPERSONATE, proxy=proxy,
                           timeout=config.UPLOAD_TIMEOUT)
    creds = get_upload_sts(session, cookie_header)
    with open(path, "rb") as f:
        data = f.read()
    ext = (os.path.splitext(path)[1].lstrip(".") or "jpeg").lower()
    apply_resp = apply_image_upload(session, creds, user_id, len(data), ext)
    node = _first_node(apply_resp)
    put = put_store_bytes(session, node, user_id, data)
    commit = commit_image_upload(session, creds, user_id, put["session_key"])
    uri = _dig_first(commit, ("ImageUri", "Uri", "uri")) or put["store_uri"]
    logger.info(f"图片上传完成 uri={uri} size={len(data)}")
    return {"uri": uri, "commit": commit, "store_uri": put["store_uri"]}


def _dig_first(obj, keys):
    """递归找第一个命中 keys 的值(commit 响应字段层级真机再定,先容错取)。"""
    if isinstance(obj, dict):
        for k in keys:
            if k in obj and obj[k]:
                return obj[k]
        for v in obj.values():
            got = _dig_first(v, keys)
            if got:
                return got
    elif isinstance(obj, list):
        for v in obj:
            got = _dig_first(v, keys)
            if got:
                return got
    return None


# ════════════════════════════════════════════════════════════════════════════
# 离线自测:AWS SigV4 官方向量(证明 AWS4 数学没错)
# ════════════════════════════════════════════════════════════════════════════
def selftest() -> bool:
    """AWS 官方 SigV4 'get-vanilla' 已知答案:host=example.amazonaws.com,
    key=AKIDEXAMPLE/wJalr... region=us-east-1 service=service date=20150830T123600Z,
    期望 Signature=5fa00fa31553b73ebf1942676e86291e8372ff2a2260956d9b8aae1d763fbf31。"""
    amz = "20150830T123600Z"
    hdr = {"host": "example.amazonaws.com", "x-amz-date": amz}
    authz, sig = signing.aws4_authorization(
        method="GET", uri="/", query={}, headers=hdr,
        payload_hash=signing.sha256_hex(b""),
        access_key="AKIDEXAMPLE", secret_key="wJalrXUtnFEMI/K7MDENG+bPxRfiCYEXAMPLEKEY",
        region="us-east-1", service="service", amz_date=amz, datestamp="20150830",
        signed_headers="host;x-amz-date")
    expect = "5fa00fa31553b73ebf1942676e86291e8372ff2a2260956d9b8aae1d763fbf31"
    ok_sig = (sig == expect)

    # 我们的 _aws4_headers 包装也跑一遍(带 STS token 的路径),只查结构不查值
    creds = {"AccessKeyID": "AKIDEXAMPLE",
             "SecretAccessKey": "wJalrXUtnFEMI/K7MDENG+bPxRfiCYEXAMPLEKEY",
             "SessionToken": "TOKEN"}
    h = _aws4_headers("GET", "vod.bytedanceapi.com", "/",
                      {"Action": "ApplyUploadInner", "s": "x"}, {"referer": "r"}, b"", creds, "vod")
    ok_wrap = ("Authorization" in h and "x-amz-security-token" in h
               and "AWS4-HMAC-SHA256" in h["Authorization"] and "/vod/aws4_request" in h["Authorization"])
    ok_crc = (_crc32_hex(b"123456789") == "cbf43926")  # CRC-32 标准向量

    print("=== douyin uploader 离线自测 ===")
    print(f"  [{'PASS' if ok_sig else 'FAIL'}] AWS SigV4 官方向量 (get-vanilla)  得到 {sig[:16]}…")
    print(f"  [{'PASS' if ok_wrap else 'FAIL'}] _aws4_headers 包装(含 STS token / service=vod)")
    print(f"  [{'PASS' if ok_crc else 'FAIL'}] CRC32 标准向量 '123456789'→cbf43926")
    ok = ok_sig and ok_wrap and ok_crc
    print(f"=== 结论: {'全通过 ✅ AWS4 数学与包装正确' if ok else '有失败 ❌'} ===")
    return ok


if __name__ == "__main__":
    import sys
    try:
        from shared.io_utf8 import setup_utf8
        setup_utf8()
    except Exception:
        pass
    sys.exit(0 if selftest() else 1)
