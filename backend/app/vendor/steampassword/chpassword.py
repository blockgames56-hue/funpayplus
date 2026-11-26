import asyncio
import base64
import logging
from typing import Dict, Optional

import pydantic.v1 as pydantic
import rsa
from lxml.html import document_fromstring
from yarl import URL

from app.steam_security import MobileConfirmations as SteamTrade
from app.steam_security import NotFoundMobileConfirmationError
from app.vendor.pysteamauth.errors import check_steam_error
from app.vendor.steampassword.exceptions import ErrorSteamPasswordChange
from app.vendor.steampassword.schemas import (
    PasswordChangeParams,
    RSAKey,
)
from app.vendor.steampassword.steam import CustomSteam

LOGGER = logging.getLogger("funpay.steam-rent")


class SteamPasswordChange:

    BROWSER = (
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/'
        '537.36 (KHTML, like Gecko) Chrome/83.0.4103.116 Safari/537.36'
    )
    # Steam показывает запрос подтверждения в мобильном Guard не мгновенно:
    # ждём до ~100 секунд перед выводом аккаунта в карантин.
    CONFIRM_RETRY_COUNT = 25
    CONFIRM_RETRY_DELAY_SECONDS = 4

    def __init__(self, steam: CustomSteam):
        self._steam = steam
        self._steam_trade = SteamTrade(steam)

    async def _receive_password_change_params(self) -> PasswordChangeParams:
        response = await self._steam.raw_request(
            method='GET',
            url='https://help.steampowered.com/wizard/HelpChangePassword?redir=store/account/',
            headers={
                'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,'
                          'image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.9',
                'Referer': 'https://store.steampowered.com/',
                'User-Agent': self.BROWSER,
            },
            allow_redirects=True,
        )
        # Redirect
        if response.history:
            try:
                return PasswordChangeParams(**URL(response.real_url).query)
            except pydantic.ValidationError:
                pass
        html = await response.text()
        page = document_fromstring(html)
        errors = page.cssselect('#error_description')
        if errors:
            raise ErrorSteamPasswordChange(errors[0].text)
        raise ErrorSteamPasswordChange('Password change error')

    async def _login_info_enter_code(self, data: PasswordChangeParams):
        await self._steam.raw_request(
            method='GET',
            url='https://help.steampowered.com/en/wizard/HelpWithLoginInfoEnterCode',
            params={
                's': data.s,
                'account': data.account,
                'reset': data.reset,
                'lost': data.lost,
                'issueid': data.issueid,
                'sessionid': await self._steam.sessionid('help.steampowered.com'),
                'wizard_ajax': 1,
                'gamepad': 0,
            },
            headers={
                'Accept': '*/*',
                'X-Requested-With': 'XMLHttpRequest',
                'User-Agent': self.BROWSER,
            }
        )

    async def _send_account_recovery_code(self, data: PasswordChangeParams) -> Optional[int]:
        response = await self._steam.json_request(
            method='POST',
            url='https://help.steampowered.com/en/wizard/AjaxSendAccountRecoveryCode',
            data={
                'sessionid': await self._steam.sessionid('help.steampowered.com'),
                'wizard_ajax': '1',
                'gamepad': '0',
                's': data.s,
                'method': '8',
                'link': '',
                'n': '1',
            },
            headers={
                'Accept': '*/*',
                'Content-Type': 'application/x-www-form-urlencoded; charset=UTF-8',
                'Origin': 'https://help.steampowered.com',
                'Referer': (
                    'https://help.steampowered.com/ru/wizard/HelpWithLoginInfoEnterCode'
                    f'?s={data.s}&account={data.account}&reset={data.reset}'
                    f'&lost={data.lost}&issueid={data.issueid}'
                ),
                'X-Requested-With': 'XMLHttpRequest',
                'User-Agent': self.BROWSER,
            }
        )
        if response.get('errorMsg'):
            raise ErrorSteamPasswordChange(response['errorMsg'])
        success = response.get('success')
        if success is None:
            LOGGER.info(
                'AjaxSendAccountRecoveryCode: нет поля success, keys=%s',
                list(response.keys()),
            )
            return None
        if isinstance(success, int):
            check_steam_error(response['success'])
        if not bool(success):
            return None

        def norm_id(value):
            if value is None:
                return None
            try:
                return int(float(value))
            except (TypeError, ValueError):
                text = str(value).strip()
                return int(text) if text.isdigit() else None

        def pick_id(obj):
            if not isinstance(obj, dict):
                return None
            for key in (
                'creator_id', 'creatorID', 'cid', 'gid', 'request_id',
                'confirmation_id', 'conf_id', 'confId',
            ):
                found = norm_id(obj.get(key))
                if found is not None:
                    return found
            inner = obj.get('data')
            if isinstance(inner, dict):
                return pick_id(inner)
            return None

        def walk_ids(node, found, depth=0):
            if depth > 14:
                return
            if isinstance(node, dict):
                for key, value in node.items():
                    lowered = str(key).lower()
                    if any(
                        part in lowered
                        for part in (
                            'creator_id', 'creatorid', 'confirmation',
                            'conf_id', 'confid', 'gid',
                        )
                    ):
                        found_id = norm_id(value)
                        if found_id is not None:
                            found.append(found_id)
                    walk_ids(value, found, depth + 1)
            elif isinstance(node, list):
                for value in node:
                    walk_ids(value, found, depth + 1)

        creator = pick_id(response)
        if creator is None:
            bag = []
            walk_ids(response, bag)
            if bag:
                creator = bag[0]
                LOGGER.info('AjaxSendAccountRecoveryCode: creator_id из обхода JSON: %s', creator)
        if creator is None:
            LOGGER.info(
                'AjaxSendAccountRecoveryCode: creator_id не найден (ответ OK), keys=%s',
                list(response.keys()),
            )
        return creator

    async def _poll_account_recovery_confirmation(self, data: PasswordChangeParams) -> Dict[str, bool]:
        response = await self._steam.json_request(
            method='POST',
            url='https://help.steampowered.com/en/wizard/AjaxPollAccountRecoveryConfirmation',
            data={
                'sessionid': await self._steam.sessionid('help.steampowered.com'),
                'wizard_ajax': 1,
                's': data.s,
                'reset': data.reset,
                'lost': data.lost,
                'method': 8,
                'issueid': data.issueid,
                'gamepad': 0,
            },
            headers={
                'Accept': '*/*',
                'Content-Type': 'application/x-www-form-urlencoded; charset=UTF-8',
                'Origin': 'https://help.steampowered.com',
                'User-Agent': self.BROWSER,
                'X-Requested-With': 'XMLHttpRequest',
            },
        )
        if response.get('errorMsg'):
            raise ErrorSteamPasswordChange(response['errorMsg'])
        return response

    async def _verify_account_recovery_code(self, data: PasswordChangeParams):
        response = await self._steam.json_request(
            method='GET',
            url='https://help.steampowered.com/en/wizard/AjaxVerifyAccountRecoveryCode',
            params={
                'code': '',
                's': data.s,
                'reset': data.reset,
                'lost': data.lost,
                'method': 8,
                'issueid': data.issueid,
                'sessionid': await self._steam.sessionid('help.steampowered.com'),
                'wizard_ajax': 1,
                'gamepad': 0,
            },
            headers={
                'Accept': '*/*',
                'User-Agent': self.BROWSER,
                'X-Requested-With': 'XMLHttpRequest',
            },
        )
        if response.get('errorMsg'):
            raise ErrorSteamPasswordChange(response['errorMsg'])

    async def _account_recovery_get_next_step(self, data: PasswordChangeParams):
        response = await self._steam.json_request(
            method='POST',
            url='https://help.steampowered.com/en/wizard/AjaxAccountRecoveryGetNextStep',
            data={
                'sessionid': await self._steam.sessionid('help.steampowered.com'),
                'wizard_ajax': 1,
                's': data.s,
                'account': data.account,
                'reset': data.reset,
                'issueid': data.issueid,
                'lost': 2,
            },
            headers={
                'Accept': '*/*',
                'X-Requested-With': 'XMLHttpRequest',
                'Content-Type': 'application/x-www-form-urlencoded; charset=UTF-8',
                'Origin': 'https://help.steampowered.com',
                'User-Agent': self.BROWSER,
            }
        )
        if response.get('errorMsg'):
            raise ErrorSteamPasswordChange(response['errorMsg'])

    async def _get_rsa_key(self) -> RSAKey:
        response = await self._steam.json_request(
            method='POST',
            url='https://help.steampowered.com/en/login/getrsakey/',
            data={
                'sessionid': await self._steam.sessionid('help.steampowered.com'),
                'username': self._steam.login,
            },
            headers={
                'Accept': '*/*',
                'Content-Type': 'application/x-www-form-urlencoded; charset=UTF-8',
                'Origin': 'https://help.steampowered.com',
                'X-Requested-With': 'XMLHttpRequest',
                'User-Agent': self.BROWSER,
            }
        )
        if response.get('errorMsg'):
            raise ErrorSteamPasswordChange(response['errorMsg'])
        return RSAKey.parse_obj(response)

    def _encrypt_password(self, password: str, mod: str, exp: str) -> str:
        publickey_exp = int(exp, 16)  # type: ignore
        publickey_mod = int(mod, 16)  # type: ignore
        public_key = rsa.PublicKey(n=publickey_mod, e=publickey_exp)
        encrypted_password = rsa.encrypt(
            message=password.encode('ascii'),
            pub_key=public_key,
        )
        encrypted_password64 = base64.b64encode(encrypted_password)
        return str(encrypted_password64, 'utf8')

    async def _recovery_verify_password(
            self,
            data: PasswordChangeParams,
            encrypted_password: str,
            rsatimestamp: int
    ):
        response = await self._steam.json_request(
            method='POST',
            url='https://help.steampowered.com/en/wizard/AjaxAccountRecoveryVerifyPassword/',
            data={
                'sessionid': await self._steam.sessionid('help.steampowered.com'),
                's': data.s,
                'lost': 2,
                'reset': 1,
                'password': encrypted_password,
                'rsatimestamp': rsatimestamp,
            },
            headers={
                'Accept': '*/*',
                'Content-Type': 'application/x-www-form-urlencoded; charset=UTF-8',
                'Origin': 'https://help.steampowered.com',
                'X-Requested-With': 'XMLHttpRequest',
                'User-Agent': self.BROWSER,
            }
        )
        if response.get('errorMsg'):
            raise ErrorSteamPasswordChange(response['errorMsg'])

    async def _check_password_available(self, password: str):
        response = await self._steam.json_request(
            url='https://help.steampowered.com/en/wizard/AjaxCheckPasswordAvailable/',
            method='POST',
            data={
                'sessionid': await self._steam.sessionid('help.steampowered.com'),
                'wizard_ajax': 1,
                'password': password,
            },
            headers={
                'Origin': 'https://help.steampowered.com',
                'User-Agent': self.BROWSER,
            },
        )
        if not response['available']:
            raise ErrorSteamPasswordChange('Not password available')

    async def _change_password_request(self, data: PasswordChangeParams, encrypted_password: str, rsatimestamp: int):
        response = await self._steam.json_request(
            method='POST',
            url='https://help.steampowered.com/ru/wizard/AjaxAccountRecoveryChangePassword/',
            data={
                'sessionid': await self._steam.sessionid('help.steampowered.com'),
                'wizard_ajax': 1,
                's': data.s,
                'account': data.account,
                'password': encrypted_password,
                'rsatimestamp': rsatimestamp,
            },
            headers={
                'Accept': '*/*',
                'Content-Type': 'application/x-www-form-urlencoded; charset=UTF-8',
                'Origin': 'https://help.steampowered.com',
                'X-Requested-With': 'XMLHttpRequest',
                'User-Agent': self.BROWSER,
            },
        )
        if response.get('errorMsg'):
            raise ErrorSteamPasswordChange(response['errorMsg'])

    async def _confirm_via_getlist(self, creator_id: Optional[int] = None) -> bool:
        """Подтверждение смены пароля через /mobileconf/getlist + ajaxop.

        Устаревший эндпоинт из библиотеки часто не находит запрос,
        поэтому работаем напрямую с cookies steamcommunity.com.
        """
        import aiohttp

        cookies = dict(await self._steam.cookies('steamcommunity.com'))
        cookies.update(
            {
                'mobileClient': 'ios',
                'mobileClientVersion': '2.0.20',
                'steamid': str(self._steam.steamid),
            }
        )
        await self._steam.sessionid('steamcommunity.com')

        device = str(self._steam.device_id or '')
        modes = (
            ['android', 'react'] if device.lower().startswith('android:')
            else ['react', 'android']
        )
        payload: dict = {}
        list_mode = 'react'
        async with aiohttp.ClientSession(cookies=cookies) as session:
            for mode in modes:
                timestamp = await self._steam.get_server_time()
                params = {
                    'p': self._steam.device_id,
                    'a': str(self._steam.steamid),
                    'k': self._steam.get_confirmation_hash(timestamp, 'conf'),
                    't': timestamp,
                    'm': mode,
                    'tag': 'conf',
                }
                try:
                    async with session.get(
                        'https://steamcommunity.com/mobileconf/getlist',
                        params=params,
                        headers={
                            'X-Requested-With': 'com.valvesoftware.android.steam.community',
                        },
                    ) as response:
                        payload = await response.json(content_type=None)
                except Exception:
                    payload = {}
                if not isinstance(payload, dict):
                    payload = {}
                if payload.get('success'):
                    list_mode = mode
                    break
                payload = {}

        if not payload.get('success'):
            LOGGER.info('[confirm_via_getlist] getlist не успешен: %s', str(payload)[:500])
            return False

        raw = payload.get('conf')
        if raw is None:
            confirmations = []
        elif isinstance(raw, list):
            confirmations = raw
        else:
            confirmations = [raw]

        target = next(
            (
                conf
                for conf in confirmations
                if isinstance(conf, dict)
                and creator_id is not None
                and str(conf.get('creator_id')) == str(creator_id)
            ),
            None,
        )
        if target is None:
            # Не берём слепо первый: ищем смену пароля/аккаунта по типу и времени.
            usable = [
                conf for conf in confirmations
                if isinstance(conf, dict) and conf.get('id') and conf.get('nonce') is not None
            ]
            keywords = (
                'password', 'парол', 'recover', 'восстан', 'account recovery',
                'аккаунт', 'credential', 'reset', 'смен',
            )
            scored = []
            for conf in usable:
                blob = ' '.join(
                    str(conf.get(key) or '')
                    for key in ('headline', 'summary', 'type_name', 'icon')
                ).lower()
                score = sum(word in blob for word in keywords)
                if score:
                    scored.append((score, int(conf.get('creation_time') or 0), conf))
            if scored:
                scored.sort(key=lambda item: (-item[0], -item[1]))
                target = scored[0][2]
            else:
                account_types = {6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18}
                target = next(
                    (
                        conf
                        for conf in sorted(
                            usable,
                            key=lambda item: int(item.get('creation_time') or 0),
                            reverse=True,
                        )
                        if int(conf.get('type') or 0) in account_types
                    ),
                    None,
                ) or (
                    sorted(usable, key=lambda item: int(item.get('creation_time') or 0),
                           reverse=True)[0]
                    if usable
                    else None
                )
        if not target:
            LOGGER.info('[confirm_via_getlist] подтверждение не найдено')
            return False

        conf_id = str(target.get('id'))
        nonce = target.get('nonce')
        if not conf_id or nonce is None:
            return False

        referer = (
            'https://steamcommunity.com/mobileconf/conf'
            if list_mode == 'android'
            else 'https://steamcommunity.com/mobileconf/react'
        )
        headers = {
            'Origin': 'https://steamcommunity.com',
            'Referer': referer,
            'X-Requested-With': 'com.valvesoftware.android.steam.community',
            'Accept': 'application/json, text/javascript, */*; q=0.01',
            'User-Agent': (
                'Mozilla/5.0 (Linux; Android 11; Build/RQ3A) '
                'AppleWebKit/537.36 Mobile Safari/537.36'
            ),
        }
        allow_modes = [list_mode] + [mode for mode in ('android', 'react') if mode != list_mode]

        for mode in allow_modes:
            timestamp = await self._steam.get_server_time()
            params = {
                'op': 'allow',
                'p': self._steam.device_id,
                'a': str(self._steam.steamid),
                'k': self._steam.get_confirmation_hash(timestamp, 'allow'),
                't': timestamp,
                'm': mode,
                'tag': 'allow',
                'cid': conf_id,
                'ck': str(nonce),
            }
            async with aiohttp.ClientSession(cookies=cookies, headers=headers) as session:
                try:
                    async with session.get(
                        'https://steamcommunity.com/mobileconf/ajaxop', params=params
                    ) as response:
                        result = await response.json(content_type=None)
                except Exception:
                    result = {}
            if isinstance(result, dict) and result.get('success'):
                return True

        for mode in allow_modes:
            timestamp = await self._steam.get_server_time()
            session_id = await self._steam.sessionid('steamcommunity.com')
            async with aiohttp.ClientSession(cookies=cookies, headers=headers) as session:
                try:
                    async with session.post(
                        'https://steamcommunity.com/mobileconf/multiajaxop',
                        data={
                            'op': 'allow',
                            'sessionid': session_id,
                            'cid[]': conf_id,
                            'ck[]': str(nonce),
                            'a': str(self._steam.steamid),
                            'k': self._steam.get_confirmation_hash(timestamp, 'allow'),
                            't': timestamp,
                            'm': mode,
                            'tag': 'allow',
                            'p': self._steam.device_id,
                        },
                    ) as response:
                        result = await response.json(content_type=None)
                except Exception:
                    result = {}
            if isinstance(result, dict) and result.get('success'):
                return True
        return False

    async def change(self, new_password: str):
        if not new_password:
            raise ValueError('Empty new password')
        if new_password == self._steam.password:
            raise ValueError('New password is equal old password')

        await self._steam.login_to_steam()

        params = await self._receive_password_change_params()

        await self._login_info_enter_code(params)
        creator_id = await self._send_account_recovery_code(params)

        # Steam часто показывает запрос в приложении не мгновенно.
        await asyncio.sleep(4)
        try:
            await self._poll_account_recovery_confirmation(params)
        except Exception as pre_poll:
            LOGGER.debug('Poll перед подтверждением Guard: %s', pre_poll)

        confirmed = False
        for attempt in range(self.CONFIRM_RETRY_COUNT):
            try:
                if await self._confirm_via_getlist(creator_id):
                    confirmed = True
                    LOGGER.info(
                        'Подтверждение смены пароля получено (попытка %d)', attempt + 1
                    )
                    break
            except Exception as error:
                LOGGER.info('Подтверждение Guard, попытка %d: %s', attempt + 1, error)
            await asyncio.sleep(self.CONFIRM_RETRY_DELAY_SECONDS)
        if not confirmed:
            waited = self.CONFIRM_RETRY_COUNT * self.CONFIRM_RETRY_DELAY_SECONDS + 4
            raise NotFoundMobileConfirmationError(
                f'Подтверждение Steam Guard не появилось за ~{waited} секунд. '
                'Проверьте maFile (shared_secret + identity_secret + device_id) '
                'и что на телефоне в Steam есть запрос на смену пароля.'
            )

        await self._poll_account_recovery_confirmation(params)
        await self._verify_account_recovery_code(params)
        await self._account_recovery_get_next_step(params)

        key = await self._get_rsa_key()
        await self._recovery_verify_password(
            data=params,
            encrypted_password=self._encrypt_password(self._steam.password, key.mod, key.exp),
            rsatimestamp=key.timestamp,
        )

        key = await self._get_rsa_key()
        await self._check_password_available(new_password)
        await self._change_password_request(
            data=params,
            encrypted_password=self._encrypt_password(new_password, key.mod, key.exp),
            rsatimestamp=key.timestamp,
        )
