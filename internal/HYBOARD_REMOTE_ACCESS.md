# Удалённый доступ к HyBoard без SSH-туннеля

Эта схема позволяет открывать HyBoard с Android и компьютера напрямую через HTTPS, не публикуя внутренний порт панели и не вмешиваясь в VPN-трафик.

```text
Браузер → HTTPS/mTLS TCP 8443 → Nginx → 127.0.0.1:28474 → HyBoard

VPN-клиент → Hysteria2 UDP 443 → hysteria-server
VLESS-клиент → Xray TCP 443 → Xray
```

Панель по-прежнему слушает только `127.0.0.1:28474`. На публичном TCP/8443 Nginx сначала проверяет персональный клиентский сертификат, а затем HyBoard запрашивает собственный пароль администратора. TCP/443, UDP/443, WireGuard и FreeTurn эта схема не меняет.

## Почему используется mTLS

У сервера нет домена. Публичный IP-сертификат Let's Encrypt защищает транспорт и позволяет браузеру проверить сервер, а клиентский сертификат ограничивает круг устройств, которым Nginx вообще покажет страницу входа. Один пароль HyBoard не является единственным публичным рубежом защиты.

Клиентский сертификат следует выпускать отдельно для каждого доверенного устройства. При потере устройства его сертификат нужно отозвать или заменить доверенный клиентский CA.

## Состав установки

- `panel/deploy/remote-access-baseline.sh` фиксирует порты, unit-файлы, checksum базы пользователей Hysteria и peers WireGuard до изменений;
- `panel/deploy/create-hyboard-client-pki.sh` создаёт отдельный клиентский CA и первый Android-сертификат;
- `panel/deploy/nginx-hyboard-mtls.conf.template` публикует только TCP/80 для ACME и TCP/8443 для панели;
- `panel/deploy/setup-hyboard-mtls.sh` включает конфигурацию Nginx и deploy-hook его перезагрузки после продления сертификата.

Скрипты намеренно не устанавливают пакеты и не выпускают публичный сертификат молча: эти действия зависят от окружения и должны выполняться поэтапно с проверкой baseline.

## Порядок развёртывания

1. Создать snapshot у провайдера, если он доступен.
2. Запустить baseline в новый каталог `/root/hyboard-backups/remote-access-preinstall-DATE`.
3. Убедиться, что TCP/80 и TCP/8443 свободны, HyBoard слушает только loopback, Hysteria занимает UDP/443, а Xray — TCP/443.
4. Установить Nginx и актуальный Certbot из официального источника.
5. Выпустить публичный сертификат для IP-адреса с именем lineage `hyboard-ip`. Не помещать реальный IP или email в репозиторий.
6. Создать два случайных пароля в файлах с режимом `0600`: пароль клиентского CA и пароль экспортируемого PKCS#12.
7. Запустить `create-hyboard-client-pki.sh`, затем `setup-hyboard-mtls.sh`.
8. Проверить `nginx -t`, endpoint с клиентским сертификатом, отказ без сертификата и тестовое продление Certbot.
9. Скопировать зашифрованный `.p12`, клиентский CA и его зашифрованный приватный ключ в off-server recovery-хранилище. Удалить временные открытые ключи с VPS.

Пример вызовов после установки Nginx, Certbot и выпуска публичного сертификата:

```bash
sudo bash panel/deploy/remote-access-baseline.sh \
  /root/hyboard-backups/remote-access-preinstall-DATE

sudo bash panel/deploy/create-hyboard-client-pki.sh \
  /root/private-input/ca-pass \
  /root/private-input/p12-pass \
  /root/hyboard-client-pki-DATE

sudo bash panel/deploy/setup-hyboard-mtls.sh \
  SERVER_IP \
  panel/deploy/nginx-hyboard-mtls.conf.template \
  /root/hyboard-backups/remote-access-preinstall-DATE
```

## Установка на Android

1. Передать `hyboard-android-primary.p12` на телефон по USB, локальной передаче файлов или через другое доверенное зашифрованное хранилище. Не отправлять его в открытый чат.
2. Открыть системные настройки Android: «Безопасность» → «Дополнительные настройки безопасности» → «Установить сертификат» → «VPN и сертификат приложения». Названия пунктов различаются у производителей.
3. Выбрать `.p12`, ввести пароль контейнера и дать сертификату понятное имя, например `HyBoard Android`.
4. Открыть в браузере `https://SERVER_IP:8443`. Если Android попросит выбрать сертификат, выбрать `HyBoard Android`.
5. Войти с обычным паролем администратора HyBoard.
6. После успешной установки удалить копию `.p12` из папки загрузок телефона. Установленный ключ останется в защищённом хранилище Android.

Не устанавливать на телефон `client-ca.key`: это ключ восстановления и выпуска новых сертификатов, а не клиентский файл.

## Резервная копия и восстановление

В зашифрованном off-server хранилище должны находиться:

- `client-ca.crt`;
- зашифрованный `client-ca.key` и его пароль в отдельном менеджере паролей;
- клиентские `.p12` и их пароли либо возможность перевыпустить их;
- приватный backup HyBoard и Hysteria2, описанный в файлах внутреннего контекста.

Публичный сертификат Let's Encrypt переносить необязательно: на новом сервере его безопаснее выпустить заново. После нештатной потери VPS серверные и клиентские credentials следует считать потенциально скомпрометированными и ротировать.

## Проверка после установки

```bash
nginx -t
systemctl is-active nginx hyboard hyboard-helper hysteria-server x-ui
systemctl is-active hyboard-expire.timer snap.certbot.renew.timer
ss -ltnp
ss -lunp
certbot renew --dry-run --no-random-sleep-on-renew --cert-name hyboard-ip
```

Ожидаемые инварианты:

- HyBoard: только `127.0.0.1:28474`;
- Nginx: TCP/80 и TCP/8443;
- Xray: TCP/443;
- Hysteria2: UDP/443;
- checksum `/etc/hysteria/users.json` и число peers `wg0` совпадают с baseline;
- запрос к TCP/8443 без клиентского сертификата отклоняется.

## Откат

Перед откатом проверить точный каталог baseline. Удаляется только site-конфигурация HyBoard в Nginx; VPN-конфиги и firewall не меняются.

```bash
sudo rm /etc/nginx/sites-enabled/hyboard
sudo mv /root/hyboard-backups/remote-access-preinstall-DATE/nginx-default.enabled \
  /etc/nginx/sites-enabled/default
sudo nginx -t
sudo systemctl reload nginx
```

Если файла `nginx-default.enabled` в baseline не было, шаг `mv` пропускается. Удалять Nginx, Certbot или сертификаты следует только после отдельной проверки, что их не использует другой сервис.
