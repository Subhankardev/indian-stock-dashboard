"""Upstox Market Data Feed V3 wire format (protobuf), without the Upstox SDK.

The schema is the one shipped in Upstox's MIT-licensed Python SDK
(``upstox_client/feeder/proto/MarketDataFeedV3_pb2.py``, Copyright 2023 Upstox).
A readable copy lives next to this file in ``MarketDataFeedV3.proto``; the
serialized descriptor below is the exact same schema, so no ``protoc`` step
is needed.
"""
from __future__ import annotations

import base64
from typing import Any, Optional

from google.protobuf import descriptor_pb2, descriptor_pool, message_factory
from google.protobuf import wrappers_pb2  # noqa: F401  (dependency of the schema)

_DESCRIPTOR_B64 = (
    "ChZNYXJrZXREYXRhRmVlZFYzLnByb3RvEixjb20udXBzdG94Lm1hcmtldGRhdGFmZWVkZXJ2M3VkYXBpLnJwYy5wcm90bxoe"
    "Z29vZ2xlL3Byb3RvYnVmL3dyYXBwZXJzLnByb3RvImQKBExUUEMSCwoDbHRwGAEgASgBEgsKA2x0dBgCIAEoAxILCgNsdHEY"
    "AyABKAMSCgoCY3AYBCABKAESKQoDaWVwGAUgASgLMhwuZ29vZ2xlLnByb3RvYnVmLkRvdWJsZVZhbHVlIlcKC01hcmtldExl"
    "dmVsEkgKC2JpZEFza1F1b3RlGAEgAygLMjMuY29tLnVwc3RveC5tYXJrZXRkYXRhZmVlZGVydjN1ZGFwaS5ycGMucHJvdG8u"
    "UXVvdGUiTgoKTWFya2V0T0hMQxJACgRvaGxjGAEgAygLMjIuY29tLnVwc3RveC5tYXJrZXRkYXRhZmVlZGVydjN1ZGFwaS5y"
    "cGMucHJvdG8uT0hMQyI/CgVRdW90ZRIMCgRiaWRRGAEgASgDEgwKBGJpZFAYAiABKAESDAoEYXNrURgDIAEoAxIMCgRhc2tQ"
    "GAQgASgBIlYKDE9wdGlvbkdyZWVrcxINCgVkZWx0YRgBIAEoARINCgV0aGV0YRgCIAEoARINCgVnYW1tYRgDIAEoARIMCgR2"
    "ZWdhGAQgASgBEgsKA3JobxgFIAEoASJpCgRPSExDEhAKCGludGVydmFsGAEgASgJEgwKBG9wZW4YAiABKAESDAoEaGlnaBgD"
    "IAEoARILCgNsb3cYBCABKAESDQoFY2xvc2UYBSABKAESCwoDdm9sGAYgASgDEgoKAnRzGAcgASgDIukDCg5NYXJrZXRGdWxs"
    "RmVlZBJACgRsdHBjGAEgASgLMjIuY29tLnVwc3RveC5tYXJrZXRkYXRhZmVlZGVydjN1ZGFwaS5ycGMucHJvdG8uTFRQQxJO"
    "CgttYXJrZXRMZXZlbBgCIAEoCzI5LmNvbS51cHN0b3gubWFya2V0ZGF0YWZlZWRlcnYzdWRhcGkucnBjLnByb3RvLk1hcmtl"
    "dExldmVsElAKDG9wdGlvbkdyZWVrcxgDIAEoCzI6LmNvbS51cHN0b3gubWFya2V0ZGF0YWZlZWRlcnYzdWRhcGkucnBjLnBy"
    "b3RvLk9wdGlvbkdyZWVrcxJMCgptYXJrZXRPSExDGAQgASgLMjguY29tLnVwc3RveC5tYXJrZXRkYXRhZmVlZGVydjN1ZGFw"
    "aS5ycGMucHJvdG8uTWFya2V0T0hMQxILCgNhdHAYBSABKAESCwoDdnR0GAYgASgDEgoKAm9pGAcgASgBEgoKAml2GAggASgB"
    "EgsKA3RicRgJIAEoARILCgN0c3EYCiABKAESCwoDaWVwGAsgASgBEgoKAnJwGAwgASgBEgsKA2llcRgNIAEoAxIQCghpaXFU"
    "b3RhbBgOIAEoAxIMCgRpaXFNGA8gASgDEhMKC2Nhc0VsaWdpYmxlGBAgASgIIp8BCg1JbmRleEZ1bGxGZWVkEkAKBGx0cGMY"
    "ASABKAsyMi5jb20udXBzdG94Lm1hcmtldGRhdGFmZWVkZXJ2M3VkYXBpLnJwYy5wcm90by5MVFBDEkwKCm1hcmtldE9ITEMY"
    "AiABKAsyOC5jb20udXBzdG94Lm1hcmtldGRhdGFmZWVkZXJ2M3VkYXBpLnJwYy5wcm90by5NYXJrZXRPSExDIr0BCghGdWxs"
    "RmVlZBJQCghtYXJrZXRGRhgBIAEoCzI8LmNvbS51cHN0b3gubWFya2V0ZGF0YWZlZWRlcnYzdWRhcGkucnBjLnByb3RvLk1h"
    "cmtldEZ1bGxGZWVkSAASTgoHaW5kZXhGRhgCIAEoCzI7LmNvbS51cHN0b3gubWFya2V0ZGF0YWZlZWRlcnYzdWRhcGkucnBj"
    "LnByb3RvLkluZGV4RnVsbEZlZWRIAEIPCg1GdWxsRmVlZFVuaW9uIpgCChRGaXJzdExldmVsV2l0aEdyZWVrcxJACgRsdHBj"
    "GAEgASgLMjIuY29tLnVwc3RveC5tYXJrZXRkYXRhZmVlZGVydjN1ZGFwaS5ycGMucHJvdG8uTFRQQxJHCgpmaXJzdERlcHRo"
    "GAIgASgLMjMuY29tLnVwc3RveC5tYXJrZXRkYXRhZmVlZGVydjN1ZGFwaS5ycGMucHJvdG8uUXVvdGUSUAoMb3B0aW9uR3Jl"
    "ZWtzGAMgASgLMjouY29tLnVwc3RveC5tYXJrZXRkYXRhZmVlZGVydjN1ZGFwaS5ycGMucHJvdG8uT3B0aW9uR3JlZWtzEgsK"
    "A3Z0dBgEIAEoAxIKCgJvaRgFIAEoARIKCgJpdhgGIAEoASLXAgoERmVlZBJCCgRsdHBjGAEgASgLMjIuY29tLnVwc3RveC5t"
    "YXJrZXRkYXRhZmVlZGVydjN1ZGFwaS5ycGMucHJvdG8uTFRQQ0gAEkoKCGZ1bGxGZWVkGAIgASgLMjYuY29tLnVwc3RveC5t"
    "YXJrZXRkYXRhZmVlZGVydjN1ZGFwaS5ycGMucHJvdG8uRnVsbEZlZWRIABJiChRmaXJzdExldmVsV2l0aEdyZWVrcxgDIAEo"
    "CzJCLmNvbS51cHN0b3gubWFya2V0ZGF0YWZlZWRlcnYzdWRhcGkucnBjLnByb3RvLkZpcnN0TGV2ZWxXaXRoR3JlZWtzSAAS"
    "TgoLcmVxdWVzdE1vZGUYBCABKA4yOS5jb20udXBzdG94Lm1hcmtldGRhdGFmZWVkZXJ2M3VkYXBpLnJwYy5wcm90by5SZXF1"
    "ZXN0TW9kZUILCglGZWVkVW5pb24iMQoKU3RhdHVzSW5mbxIOCgZzdGF0dXMYASABKAkSEwoLdXBkYXRlZFRpbWUYAiABKAMi"
    "pQUKCk1hcmtldEluZm8SYgoNc2VnbWVudFN0YXR1cxgBIAMoCzJLLmNvbS51cHN0b3gubWFya2V0ZGF0YWZlZWRlcnYzdWRh"
    "cGkucnBjLnByb3RvLk1hcmtldEluZm8uU2VnbWVudFN0YXR1c0VudHJ5EmYKD2Nhc01hcmtldFN0YXR1cxgCIAMoCzJNLmNv"
    "bS51cHN0b3gubWFya2V0ZGF0YWZlZWRlcnYzdWRhcGkucnBjLnByb3RvLk1hcmtldEluZm8uQ2FzTWFya2V0U3RhdHVzRW50"
    "cnkScAoUcHJlT3BlblNlc3Npb25TdGF0dXMYAyADKAsyUi5jb20udXBzdG94Lm1hcmtldGRhdGFmZWVkZXJ2M3VkYXBpLnJw"
    "Yy5wcm90by5NYXJrZXRJbmZvLlByZU9wZW5TZXNzaW9uU3RhdHVzRW50cnkacAoSU2VnbWVudFN0YXR1c0VudHJ5EgsKA2tl"
    "eRgBIAEoCRJJCgV2YWx1ZRgCIAEoDjI6LmNvbS51cHN0b3gubWFya2V0ZGF0YWZlZWRlcnYzdWRhcGkucnBjLnByb3RvLk1h"
    "cmtldFN0YXR1czoCOAEacAoUQ2FzTWFya2V0U3RhdHVzRW50cnkSCwoDa2V5GAEgASgJEkcKBXZhbHVlGAIgASgLMjguY29t"
    "LnVwc3RveC5tYXJrZXRkYXRhZmVlZGVydjN1ZGFwaS5ycGMucHJvdG8uU3RhdHVzSW5mbzoCOAEadQoZUHJlT3BlblNlc3Np"
    "b25TdGF0dXNFbnRyeRILCgNrZXkYASABKAkSRwoFdmFsdWUYAiABKAsyOC5jb20udXBzdG94Lm1hcmtldGRhdGFmZWVkZXJ2"
    "M3VkYXBpLnJwYy5wcm90by5TdGF0dXNJbmZvOgI4ASLpAgoMRmVlZFJlc3BvbnNlEkAKBHR5cGUYASABKA4yMi5jb20udXBz"
    "dG94Lm1hcmtldGRhdGFmZWVkZXJ2M3VkYXBpLnJwYy5wcm90by5UeXBlElQKBWZlZWRzGAIgAygLMkUuY29tLnVwc3RveC5t"
    "YXJrZXRkYXRhZmVlZGVydjN1ZGFwaS5ycGMucHJvdG8uRmVlZFJlc3BvbnNlLkZlZWRzRW50cnkSEQoJY3VycmVudFRzGAMg"
    "ASgDEkwKCm1hcmtldEluZm8YBCABKAsyOC5jb20udXBzdG94Lm1hcmtldGRhdGFmZWVkZXJ2M3VkYXBpLnJwYy5wcm90by5N"
    "YXJrZXRJbmZvGmAKCkZlZWRzRW50cnkSCwoDa2V5GAEgASgJEkEKBXZhbHVlGAIgASgLMjIuY29tLnVwc3RveC5tYXJrZXRk"
    "YXRhZmVlZGVydjN1ZGFwaS5ycGMucHJvdG8uRmVlZDoCOAEqOAoEVHlwZRIQCgxpbml0aWFsX2ZlZWQQABINCglsaXZlX2Zl"
    "ZWQQARIPCgttYXJrZXRfaW5mbxACKkUKC1JlcXVlc3RNb2RlEggKBGx0cGMQABILCgdmdWxsX2Q1EAESEQoNb3B0aW9uX2dy"
    "ZWVrcxACEgwKCGZ1bGxfZDMwEAMqewoMTWFya2V0U3RhdHVzEhIKDlBSRV9PUEVOX1NUQVJUEAASEAoMUFJFX09QRU5fRU5E"
    "EAESDwoLTk9STUFMX09QRU4QAhIQCgxOT1JNQUxfQ0xPU0UQAxIRCg1DTE9TSU5HX1NUQVJUEAQSDwoLQ0xPU0lOR19FTkQQ"
    "BWIGcHJvdG8z"
)

_pool = descriptor_pool.DescriptorPool()
_pool.AddSerializedFile(wrappers_pb2.DESCRIPTOR.serialized_pb)
_file = _pool.AddSerializedFile(base64.b64decode(_DESCRIPTOR_B64))
_PKG = "com.upstox.marketdatafeederv3udapi.rpc.proto"


def _cls(name: str):
    return message_factory.GetMessageClass(_pool.FindMessageTypeByName(f"{_PKG}.{name}"))


FeedResponse = _cls("FeedResponse")
Feed = _cls("Feed")
LTPC = _cls("LTPC")

_MARKET_STATUS = {v.number: v.name for v in _pool.FindEnumTypeByName(f"{_PKG}.MarketStatus").values}
_TYPE = {v.number: v.name for v in _pool.FindEnumTypeByName(f"{_PKG}.Type").values}


def _ltpc(l) -> dict:
    return {"ltp": l.ltp, "ltt": int(l.ltt), "ltq": int(l.ltq), "prev_close": l.cp}


def _day_ohlc(market_ohlc) -> Optional[dict]:
    for o in market_ohlc.ohlc:
        if o.interval == "1d":
            return {"open": o.open, "high": o.high, "low": o.low, "close": o.close, "volume": int(o.vol)}
    return None


def normalise_feed(key: str, feed) -> dict:
    """One instrument's Feed message -> flat dict of the fields the dashboard uses."""
    union = feed.WhichOneof("FeedUnion")
    tick: dict[str, Any] = {"key": key}
    if union == "ltpc":
        tick.update(_ltpc(feed.ltpc))
    elif union == "fullFeed":
        full = feed.fullFeed
        if full.WhichOneof("FullFeedUnion") == "marketFF":
            m = full.marketFF
            tick.update(_ltpc(m.ltpc))
            tick.update({
                "oi": m.oi, "volume": int(m.vtt), "atp": m.atp,
                "total_buy_qty": m.tbq, "total_sell_qty": m.tsq, "day": _day_ohlc(m.marketOHLC),
            })
            if m.marketLevel.bidAskQuote:
                q = m.marketLevel.bidAskQuote[0]
                tick.update({"bid": q.bidP, "bid_qty": int(q.bidQ), "ask": q.askP, "ask_qty": int(q.askQ)})
        else:
            i = full.indexFF
            tick.update(_ltpc(i.ltpc))
            tick["day"] = _day_ohlc(i.marketOHLC)
    elif union == "firstLevelWithGreeks":
        f = feed.firstLevelWithGreeks
        tick.update(_ltpc(f.ltpc))
        tick.update({"oi": f.oi, "volume": int(f.vtt), "iv": f.iv,
                     "bid": f.firstDepth.bidP, "ask": f.firstDepth.askP})
    else:
        return tick
    pc = tick.get("prev_close")
    if pc:
        tick["change_pct"] = (tick["ltp"] / pc - 1) * 100
    return tick


def decode(buffer: bytes) -> dict:
    """Binary frame -> {type, ts, ticks: [..], market_status: {segment: status}}."""
    msg = FeedResponse.FromString(buffer)
    return {
        "type": _TYPE.get(msg.type, str(msg.type)),
        "ts": int(msg.currentTs),
        "ticks": [normalise_feed(k, f) for k, f in msg.feeds.items()],
        "market_status": {seg: _MARKET_STATUS.get(s, str(s)) for seg, s in msg.marketInfo.segmentStatus.items()},
    }
