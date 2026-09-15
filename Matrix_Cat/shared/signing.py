# -*- coding: utf-8 -*-
"""协议签名原语:sha256 / hmac / hkdf / ecdh-p256 / aws4 / base64。

小红书走浏览器内 _webmsxyw 加签(见 xhs_publisher),用不到这里。但抖音这类自算签名的
平台要:
  - bd-ticket-guard(抖音 create_v2 写接口)= 用 secsdk 种在 cookie 里的 EC 私钥,对
    `ticket=..&path=/web/api/media/aweme/create_v2/&timestamp=..` 做 **ECDSA-SHA256**
    (见 ecdsa_sign_der)。这条是回蚁小二 clientSign 源码实证的(2026-07-13),不是早前
    猜的 ECDH→HKDF→HMAC;a_bogus 发布接口留空即可,门在这个头上。ECDH/HKDF 那几个原语
    留着备用(secsdk 握手/别的接口可能用),但发布不走。
  - VOD / ImageX 云上传 = AWS4-HMAC-SHA256 四步链

原语都对着公开标准向量校过(AWS4 对 AWS 官方 SigV4 测试向量、HMAC 对 RFC4231、SHA256 对
FIPS180-4)。平台侧只管把各自的报文拼进来,原语在这。
"""
import base64
import hashlib
import hmac as _hmac
from urllib.parse import quote

# cryptography 只在 ECDH/HKDF 这些 EC 相关原语里用,延迟到函数内 import——这样只需要
# AWS4/hmac 的平台(纯 stdlib)不用装 cryptography,也不会因为它缺失整包 import 就崩。


def _b(x):
    return x if isinstance(x, (bytes, bytearray)) else str(x).encode("utf-8")


# ---- 哈希 / HMAC / base64 -----------------------------------------------------
def sha256(data) -> bytes:
    return hashlib.sha256(_b(data)).digest()


def sha256_hex(data) -> str:
    return hashlib.sha256(_b(data)).hexdigest()


def hmac_sha256(key, msg) -> bytes:
    return _hmac.new(_b(key), _b(msg), hashlib.sha256).digest()


def hmac_sha256_hex(key, msg) -> str:
    return _hmac.new(_b(key), _b(msg), hashlib.sha256).hexdigest()


def b64(data) -> str:
    return base64.b64encode(_b(data)).decode()


def b64decode(s) -> bytes:
    return base64.b64decode(s)


def b64url(data, pad=False) -> str:
    enc = base64.urlsafe_b64encode(_b(data))
    return enc.decode() if pad else enc.rstrip(b"=").decode()


def hkdf_sha256(ikm, length=32, salt=None, info=b"") -> bytes:
    # 注意:抖音那条是 salt=None(等价 pyca HKDF salt=None,不是 32 字节 0)。
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.kdf.hkdf import HKDF
    return HKDF(algorithm=hashes.SHA256(), length=length, salt=salt, info=_b(info)).derive(_b(ikm))


# ---- EC / ECDH P-256 (bd-ticket-guard) ---------------------------------------
def load_ec_private(pem):
    """PKCS#8 PEM → 私钥对象(security-sdk 里 s_sdk_crypt_sdk 那把)。"""
    from cryptography.hazmat.primitives import serialization
    return serialization.load_pem_private_key(_b(pem), password=None)


def server_pub_from_cert(cert_pem):
    """服务端自签证书 PEM → 公钥(证书签名本身不校验,只取公钥)。"""
    from cryptography import x509
    cert = cert_pem if isinstance(cert_pem, x509.Certificate) \
        else x509.load_pem_x509_certificate(_b(cert_pem))
    return cert.public_key()


def ecdh_shared_x(priv, peer) -> bytes:
    """自己私钥 × 对端公钥 → 共享 X(32B 大端)。peer 可以是公钥对象 / 证书对象 / 证书 PEM。"""
    from cryptography import x509
    from cryptography.hazmat.primitives.asymmetric import ec
    if isinstance(peer, (bytes, str, x509.Certificate)):
        peer = server_pub_from_cert(peer)
    return priv.exchange(ec.ECDH(), peer)


def ec_public_uncompressed(key) -> bytes:
    """0x04 || X || Y 未压缩点(bd-ticket-guard 的 ree-public-key 就是它的 base64)。"""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    pub = key.public_key() if isinstance(key, ec.EllipticCurvePrivateKey) else key
    return pub.public_bytes(serialization.Encoding.X962,
                            serialization.PublicFormat.UncompressedPoint)


def ecdsa_sign_der(priv, data) -> bytes:
    """ECDSA-SHA256 签名,DER(ASN.1)编码——跟 Node `crypto.createSign('SHA256').sign(key)`
    的默认输出逐字节一致(两边默认都是 DER,不是 raw r||s)。

    抖音 create_v2 的 bd-ticket-guard-client-data 里那截 req_sign 就是它:
        priv = cookie 'security-sdk/s_sdk_crypt_sdk' 里的 ec_privateKey(PKCS#8 PEM)
        data = f"ticket={ticket}&path=/web/api/media/aweme/create_v2/&timestamp={ts}"
    priv 可传 PEM(str/bytes)或已 load 的私钥对象。返回 DER 字节,调用方自行 base64。
    """
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import ec
    if isinstance(priv, (bytes, str)):
        priv = load_ec_private(priv)
    return priv.sign(_b(data), ec.ECDSA(hashes.SHA256()))


def ecdsa_verify_der(pub, signature, data) -> bool:
    """校验 ecdsa_sign_der 的产物(自测/自洽用)。pub 可为公钥对象或 PEM 证书。"""
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import ec
    if isinstance(pub, (bytes, str)):
        pub = server_pub_from_cert(pub)
    try:
        pub.verify(signature, _b(data), ec.ECDSA(hashes.SHA256()))
        return True
    except InvalidSignature:
        return False


# ---- AWS4-HMAC-SHA256 (VOD 云上传) -------------------------------------------
def aws4_signing_key(secret, datestamp, region, service) -> bytes:
    k = hmac_sha256("AWS4" + str(secret), datestamp)
    k = hmac_sha256(k, region)
    k = hmac_sha256(k, service)
    return hmac_sha256(k, "aws4_request")


def _canonical_query(query) -> str:
    if not query:
        return ""
    items = query.items() if isinstance(query, dict) else list(query)
    parts = [(quote(str(k), safe="-_.~"), quote(str(v), safe="-_.~")) for k, v in items]
    parts.sort()
    return "&".join(f"{k}={v}" for k, v in parts)


def aws4_authorization(*, method, uri, query, headers, payload_hash,
                       access_key, secret_key, region, service, amz_date, datestamp,
                       signed_headers=None):
    """标准 SigV4,返回 (Authorization 头值, signature)。

    抖音 VOD 的口径差异(比如 GET 不把 x-amz-content-sha256 计进签、POST 计)由平台侧决定
    传进来的 headers / payload_hash / signed_headers,这里只做标准计算。
    """
    hdr = {k.lower(): str(v).strip() for k, v in headers.items()}
    sh = signed_headers or ";".join(sorted(hdr))
    canonical_headers = "".join(f"{k}:{hdr[k]}\n" for k in sh.split(";"))
    canonical_request = "\n".join(
        [method.upper(), uri, _canonical_query(query), canonical_headers, sh, payload_hash]
    )
    scope = f"{datestamp}/{region}/{service}/aws4_request"
    string_to_sign = "\n".join(
        ["AWS4-HMAC-SHA256", amz_date, scope, sha256_hex(canonical_request)]
    )
    signature = hmac_sha256_hex(aws4_signing_key(secret_key, datestamp, region, service),
                                string_to_sign)
    authorization = (f"AWS4-HMAC-SHA256 Credential={access_key}/{scope}, "
                     f"SignedHeaders={sh}, Signature={signature}")
    return authorization, signature
