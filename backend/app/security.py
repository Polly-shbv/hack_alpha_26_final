import http.client, ipaddress, socket, ssl, os, certifi
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode, urljoin
from .errors import DomainError

def canonical_url(url):
    p=urlsplit(url)
    return urlunsplit((p.scheme.lower(),p.netloc.lower(),p.path or '/',urlencode([(k,v) for k,v in parse_qsl(p.query) if not k.lower().startswith('utm_') and k.lower() not in ('fbclid','gclid')]),''))
def public_target(url, resolver=socket.getaddrinfo):
    try:p=urlsplit(url);port=p.port
    except ValueError:raise DomainError('UNSAFE_URL','Недопустимый адрес источника')
    if len(url)>2000 or p.scheme not in ('http','https') or not p.hostname or p.username or p.password or port not in (None,80,443): raise DomainError('UNSAFE_URL','Недопустимый адрес источника')
    if ':' in p.hostname: raise DomainError('UNSAFE_URL','IPv6-адреса источников не поддерживаются')
    try: addresses={x[4][0] for x in resolver(p.hostname,p.port or (443 if p.scheme=='https' else 80),type=socket.SOCK_STREAM)}
    except OSError: raise DomainError('SOURCE_UNAVAILABLE','Не удалось загрузить материал. Проверьте ссылку или добавьте файл',502)
    if not addresses or any(not ipaddress.ip_address(a).is_global for a in addresses): raise DomainError('UNSAFE_URL','Локальные и служебные адреса запрещены')
    return p,sorted(addresses)[0]
class PinnedHTTPS(http.client.HTTPSConnection):
    def __init__(self,host,ip,port): super().__init__(host,port=port,timeout=15,context=ssl.create_default_context(cafile=os.getenv('SSL_CERT_FILE') or certifi.where())); self.pinned_ip=ip
    def connect(self):
        self.sock=self._context.wrap_socket(socket.create_connection((self.pinned_ip,self.port),15),server_hostname=self.host)
def fetch_public(url):
    # DNS is resolved once per redirect; connect to the validated IP, preserving TLS SNI.
    for _ in range(4):
        p,ip=public_target(url)
        conn=PinnedHTTPS(p.hostname,ip,p.port or 443) if p.scheme=='https' else http.client.HTTPConnection(ip,port=p.port or 80,timeout=15)
        try:
            conn.request('GET',p.path+('?' + p.query if p.query else ''),headers={'Host':p.hostname,'User-Agent':'AlfaContext/1.0','Accept-Encoding':'identity'})
            r=conn.getresponse()
            if r.status in (301,302,303,307,308): url=urljoin(url,r.getheader('Location','')); continue
            if r.status!=200: raise DomainError('SOURCE_UNAVAILABLE','Не удалось загрузить материал. Проверьте ссылку или добавьте файл',502)
            raw=r.read(10*1024*1024+1)
            if len(raw)>10*1024*1024: raise DomainError('SOURCE_TOO_LARGE','Источник превышает 10 МБ',413)
            return raw,r.getheader('Content-Type',''),canonical_url(url)
        except ssl.SSLCertVerificationError:raise DomainError('SOURCE_TLS','Не удалось проверить TLS-сертификат источника',502)
        except (OSError,http.client.HTTPException): raise DomainError('SOURCE_TIMEOUT','Источник отвечает слишком долго',502)
        finally: conn.close()
    raise DomainError('REDIRECT_LIMIT','Слишком много перенаправлений',502)
