"""Expose public profile metadata from the same page used by yt-dlp's listing."""
import re
from yt_dlp.extractor.tiktok import TikTokUserIE
from yt_dlp.utils import ExtractorError


class TikTokProfileIE(TikTokUserIE):
    def _real_extract(self, url):
        self._public_profile = self._match_id(url) if url.startswith('https://') else None
        try:
            return super()._real_extract(url)
        finally:
            self._public_profile = None

    def _profile_data(self, webpage, username):
        data = super()._get_universal_data(webpage, username)
        if data.get('webapp.user-detail'):
            return data
        # Older public profile pages use SIGI_STATE rather than universal data.
        sigi = self._get_sigi_state(webpage, username)
        module = sigi.get('UserModule') if isinstance(sigi, dict) else None
        users = module.get('users') if isinstance(module, dict) else None
        stats = module.get('stats') if isinstance(module, dict) else None
        if isinstance(users, dict):
            for key, user in users.items():
                if not isinstance(user, dict) or (str(user.get('uniqueId') or '').casefold() != username.casefold() and not(username.isdigit() and str(user.get('id') or '')==username)):
                    continue
                counters = (stats.get(key) or stats.get(str(user.get('id'))) or stats.get(username) or {}) if isinstance(stats, dict) else {}
                return {'webapp.user-detail': {'statusCode': 10222 if user.get('privateAccount') is True else 0,
                        'userInfo': {'user': user, 'stats': counters if isinstance(counters, dict) else {}}}}
        return data

    def _get_universal_data(self, webpage, display_id):
        is_profile = getattr(self, '_public_profile', None) == display_id
        data = self._profile_data(webpage, display_id) if is_profile else super()._get_universal_data(webpage, display_id)
        # Only retry an identified native TikTok challenge, once. This is the
        # same maintained handler used by TikTokBaseIE's public video path.
        if is_profile and not data.get('webapp.user-detail') and re.search(r'\bid=[\'"]cs[\'"]', webpage):
            cookies = ()
            try:
                cookies = self._solve_challenge_and_set_cookies(webpage)
                page = self._download_webpage(self._UPLOADER_URL_FORMAT % display_id, display_id,
                        note='Downloading profile with native challenge cookies', impersonate=True,
                        headers=self._generate_blockbuster_headers())
                data = self._profile_data(page, display_id)
            finally:
                for name in filter(None, cookies):
                    try:
                        self.cookiejar.clear(domain='.tiktok.com', path='/', name=name)
                    except KeyError:
                        pass
        detail = data.get('webapp.user-detail') if isinstance(data, dict) else None
        user_info = detail.get('userInfo') if isinstance(detail, dict) else None
        user = user_info.get('user') if isinstance(user_info, dict) else None
        if is_profile and isinstance(user, dict) and user.get('uniqueId') and str(user['uniqueId']).casefold() != display_id.casefold() and not(display_id.isdigit() and str(user.get('id') or '')==display_id):
            raise ExtractorError('TikTok returned a different profile than the requested username.', expected=True)
        callback = self.get_param('_aaasoft_profile')
        if callback and isinstance(user_info, dict):
            callback(user_info)
        return data

    def _download_json(self, url_or_request, *args, **kwargs):
        # The upstream profile page already supports browser-compatible requests;
        # carry that supported transport through to the account API as well.
        if isinstance(url_or_request,str) and url_or_request.split('?',1)[0]==self._API_BASE_URL:
            kwargs['impersonate']=True
            kwargs['require_impersonation']=True
            profile=self.get_param('_aaasoft_account_url')
            if profile:
                kwargs['headers']={'Referer':profile,**(kwargs.get('headers') or {})}
            self.to_screen('Requesting account page with supported browser transport')
        return super()._download_json(url_or_request,*args,**kwargs)
