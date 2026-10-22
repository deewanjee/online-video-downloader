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
