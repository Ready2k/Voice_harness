"""Delegate transport binding; never import banking prompts/tools into telephone sessions."""
import inspect

from .live_providers import LIVE_PROVIDERS, TESTED_MODELS, _nova_env, requirements

LABELS = {"openai-realtime-live": "OpenAI Realtime", "gemini-live": "Google Gemini",
          "nova-sonic-live": "Amazon Nova Sonic", "xai-voice-live": "xAI Grok Voice"}


def provider_options(env):
    return [{"provider_config_id": key, "label": LABELS[key], "model_id": TESTED_MODELS[key]["model_id"],
             "missing": requirements(key, {**env, "VOICE_HARNESS_LIVE_ASR": "openai"}, manual=True)}
            for key in LABELS]


class TelephoneAdapter:
    def __init__(self, native, provider, env):
        self.native, self.provider, self.env = native, provider, env

    async def open_session(self, config):
        config = {**config, "model_id": TESTED_MODELS[self.provider]["model_id"]}
        if self.provider == "nova-sonic-live":
            config.update(system_prompt=config["instructions"], env={**self.env, **_nova_env()}, endpointing="LOW")
        elif self.provider == "gemini-live":
            config["turn_detection"] = "server_vad"
        info = await self.native.open_session(config)
        return {**info, "provider_config_id": self.provider, "requested_model_id": config["model_id"],
                "turn_control": "controller_requested" if self.provider in {"openai-realtime-live", "xai-voice-live"} else "provider_vad_with_controller_context"}

    async def send_text(self, text, *, respond=True):
        if "respond" in inspect.signature(self.native.send_text).parameters:
            await self.native.send_text(text, respond=respond)
        elif respond:
            # Nova text turns trigger inference; don't turn connection notices into replies.
            await self.native.send_text(text)

    async def submit_tool_result(self, call_id, result, *, respond=False):
        if "respond" in inspect.signature(self.native.submit_tool_result).parameters:
            return await self.native.submit_tool_result(call_id, result, respond=respond)
        return await self.native.submit_tool_result(call_id, result)

    async def send_audio(self, data, metadata):
        await self.native.send_audio(data, metadata)

    def receive(self):
        return self.native.receive()

    async def close(self, reason):
        await self.native.close(reason)


def build_adapter(provider, env):
    if provider not in LIVE_PROVIDERS:
        raise ValueError("UNKNOWN_TELEPHONE_PROVIDER")
    if provider == "openai-realtime-live":
        from .adapters.openai_realtime import OpenAIRealtimeAdapter
        native = OpenAIRealtimeAdapter(api_key=env.get("OPENAI_API_KEY"))
    elif provider == "gemini-live":
        from .adapters.google_gemini_live import GeminiLiveAdapter
        native = GeminiLiveAdapter()
    elif provider == "nova-sonic-live":
        from .adapters.nova_sonic import NovaSonicAdapter
        native = NovaSonicAdapter()
    else:
        from .adapters.xai_voice import KEY_ENV, XaiVoiceAdapter
        native = XaiVoiceAdapter(api_key=env.get(KEY_ENV))
    return TelephoneAdapter(native, provider, env)
