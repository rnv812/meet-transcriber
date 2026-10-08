# Подпись Windows (Authenticode)

Без подписи SmartScreen предупреждает «Неизвестный издатель» при каждой
установке и обновлении. С подписью предупреждение уходит (у сертификата EV —
сразу, у OV — когда наберётся репутация загрузок).

## Что нужно

Сертификат подписи кода с закрытым ключом в файле `.pfx`:

- **OV** (Organization Validation) — дешевле, репутация SmartScreen копится со временем;
- **EV** (Extended Validation) — доверие сразу, но ключ обычно на токене или в облачном
  HSM; тогда `.pfx` не получить — нужен облачный вариант ниже.

## Как включить (сертификат в `.pfx`)

1. Перевести `.pfx` в base64 (PowerShell):

       [Convert]::ToBase64String([IO.File]::ReadAllBytes("meet.pfx")) | Set-Clipboard

2. В репозитории GitHub: Settings → Secrets and variables → Actions → New repository secret:
   - `WINDOWS_SIGN_PFX` — содержимое из буфера;
   - `WINDOWS_SIGN_PASSWORD` — пароль к `.pfx`.
3. Следующий тег `v*` соберёт подписанный установщик. В журнале шага «Build installer»
   будет «подпись Authenticode: включена» и «подписан: …» с отпечатком сертификата;
   пароль и сертификат в журнал не попадают.

Без секретов сборка идёт как раньше, без подписи («подпись Authenticode: пропущена»).

## Как устроено

- `scripts/build_release.ps1` при заданных секретах передаёт `tauri build` настройку
  `bundle.windows.signCommand` — скрипт `scripts/sign_windows.ps1`, которым Tauri
  подписывает exe приложения и установщик NSIS.
- `sign_windows.ps1` берёт `signtool` из Windows SDK раннера, кладёт `.pfx` во временный
  файл, подписывает с меткой времени (DigiCert; другой сервер — `WINDOWS_SIGN_TIMESTAMP`)
  и сразу удаляет файл; затем проверяет подпись.
- После сборки `build_release.ps1` ещё раз проверяет подписи установщика и
  `meet-desktop.exe`; неверная — выпуск не собирается.
- Движок (Python и колёса) ставится на машине пользователя через uv и не подписывается:
  это чужие, уже подписанные или проверенные по хешам файлы.

## Облачная подпись (Azure Trusted Signing и подобные)

Для ключа в облачном HSM замените в `sign_windows.ps1` вызов `signtool … /f` на
`signtool sign /dlib <Azure.CodeSigning.Dlib.dll> /dmdf <metadata.json> …` (или утилиту
своего провайдера) и заведите его секреты вместо `.pfx`. Остальное — то же.
