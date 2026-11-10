"""Telegram Bot API sender with bounded retries and secret-safe errors."""

import asyncio
from html import escape
import os
from typing import Any

import httpx


class NotificationError(RuntimeError):
    """An alert could not be delivered after bounded retries."""


class TelegramNotifier:
    def __init__(
        self,
        bot_token: str,
        chat_id: str,
        *,
        dashboard_url: str = "",
        timeout_seconds: float = 10,
        max_retries: int = 2,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        if not bot_token.strip() or not chat_id.strip():
            raise ValueError("Telegram bot token and chat id are required")
        self.bot_token = bot_token.strip()
        self.chat_id = chat_id.strip()
        self.dashboard_url = dashboard_url.strip()
        self.timeout_seconds = timeout_seconds
        self.max_retries = max(0, max_retries)
        self.transport = transport

    @classmethod
    def from_environment(cls) -> "TelegramNotifier | None":
        token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
        chat_id = os.getenv("TELEGRAM_CHAT_ID", "").strip()
        if not token and not chat_id:
            return None
        if not token or not chat_id:
            raise ValueError("Both Telegram environment values must be set")
        return cls(
            token,
            chat_id,
            dashboard_url=os.getenv("DASHBOARD_URL", ""),
            timeout_seconds=float(os.getenv("TELEGRAM_TIMEOUT_SECONDS", "10")),
            max_retries=int(os.getenv("TELEGRAM_MAX_RETRIES", "2")),
        )

    def _message(self, signal: dict[str, Any]) -> str:
        safe = lambda value: escape(str(value), quote=True)
        targets = signal.get("take_profits", ())
        confirmations = signal.get("confirmations", ())
        lines = [
            f"<b>{safe(signal.get('instrument'))} {safe(signal.get('direction'))}</b>",
            f"Strategy: {safe(signal.get('strategy'))} v{safe(signal.get('strategy_version'))}",
            f"Entry zone: {safe(signal.get('entry_zone_low'))} – {safe(signal.get('entry_zone_high'))}",
            f"Stop loss: {safe(signal.get('stop_loss'))}",
        ]
        lines.extend(
            f"Take profit {index}: {safe(target)}" for index, target in enumerate(targets, start=1)
        )
        ratio = signal.get("risk_reward_ratio", signal.get("net_reward_risk", signal.get("planned_reward_risk")))
        if signal.get("risk_filter_basis") == "after_configured_costs":
            ratio_line = f"Estimated reward/risk after configured costs: {safe(ratio)}:1"
        else:
            ratio_line = f"Planned gross reward/risk: {safe(ratio)}:1 (broker costs not included)"
        lines.extend(
            [
                ratio_line,
                f"Historical status: {safe(str(signal.get('validation_status', 'testing')).replace('_', ' '))}",
                f"Higher-timeframe context: {safe(signal.get('higher_timeframe_bias'))}",
                f"Detected: {safe(signal.get('detected_at'))}",
                "Confirmations:",
            ]
        )
        lines.extend(f"• {safe(item)}" for item in confirmations)
        lines.append("Historical and forward results are informational and do not gate alerts.")
        lines.append("This is an analysis alert, not an executed trade or a profit guarantee.")
        if self.dashboard_url:
            lines.append(f'<a href="{safe(self.dashboard_url)}">Open dashboard</a>')
        return "\n".join(lines)

    async def send_signal(self, signal: dict[str, Any]) -> None:
        endpoint = f"/bot{self.bot_token}/sendMessage"
        payload = {
            "chat_id": self.chat_id,
            "text": self._message(signal),
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }
        try:
            async with httpx.AsyncClient(
                base_url="https://api.telegram.org",
                timeout=self.timeout_seconds,
                transport=self.transport,
            ) as client:
                for attempt in range(self.max_retries + 1):
                    response = await client.post(endpoint, json=payload)
                    if response.status_code == 429 or response.status_code >= 500:
                        if attempt >= self.max_retries:
                            break
                        retry_after = 0
                        try:
                            body = response.json()
                            retry_after = int(body.get("parameters", {}).get("retry_after", 0))
                        except (ValueError, TypeError):
                            pass
                        await asyncio.sleep(min(max(retry_after, 2**attempt), 30))
                        continue
                    response.raise_for_status()
                    body = response.json()
                    if body.get("ok") is True:
                        return
                    if attempt < self.max_retries:
                        await asyncio.sleep(min(2**attempt, 8))
        except (httpx.HTTPError, ValueError, TypeError):
            pass
        raise NotificationError("Telegram alert delivery failed") from None
