"""
lead_qualifying — Lead Qualifying Agent module.

Reads new inbound leads from Google Sheets, enriches them via web search,
qualifies business fit via LLM for four platforms, writes results back to
the sheet, and sends a Telegram summary.
"""
