"""Expose public profile metadata from the same page used by yt-dlp's listing."""
from yt_dlp.extractor.tiktok import TikTokUserIE


class TikTokProfileIE(TikTokUserIE):
    def _get_universal_data(self, webpage, display_id):
        data = super()._get_universal_data(webpage, display_id)
        callback = self.get_param('_aaasoft_profile')
        detail = data.get('webapp.user-detail') if isinstance(data, dict) else None
        if callback and isinstance(detail, dict):
            callback(detail.get('userInfo') or {})
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
