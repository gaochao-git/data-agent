# Palantir Foundry Docs — /docs/foundry/aip/supported-llms

> 来源: https://www.palantir.com/docs/foundry/aip/supported-llms
> 抓取: 2026-10-17（本地参考副本，版权属 Palantir）

---

# Supported LLMs

Palantir AIP supports a wide range of LLMs (large language models) and text embedding models from leading providers, including xAI, OpenAI, Anthropic, Meta, and Google. Supported models are listed on this page and can be used across the Palantir platform to power AIP workflows, though [availability](#llm-availability-prerequisites) may differ across enrollments (for instance, due to [georestriction](#llm-availability-by-geography)).

Enrollment administrators manage model families through Control Panel. When a subprocessor is enabled, Palantir may enable additional model families that meet the eligibility requirements under the accepted terms and conditions. Learn how to [configure the selection of LLMs available for use on your enrollment.](/docs/foundry/aip/enable-aip-features/#enable-llms)

## Available LLMs

The following LLMs are supported for use with AIP, subject to [enrollment availability](#llm-availability-prerequisites). For models scheduled for deprecation, review the migration deadlines and recommended replacements in [Upgrade Assistant](/docs/foundry/model-catalog/model-deprecation/).

- [Grok 4.3 ↗](https://docs.x.ai/developers/models/grok-4.3)
- [Grok 4.5 ↗](https://docs.x.ai/developers/models/grok-4.5)
- [Grok 4.6 ↗](https://docs.x.ai/developers/models/grok-4.6)
- [Grok 4.7 ↗](https://docs.x.ai/developers/models/grok-4.7)
- [Grok 420 Reasoning Latest ↗](https://docs.x.ai/developers/models/grok-4.20-reasoning)
- [Grok 420 Non-Reasoning Latest ↗](https://docs.x.ai/developers/models/grok-4.20-non-reasoning)
- [Grok Build 0.1 ↗](https://x.ai/news/grok-build-0-1)
- [GPT-4o ↗](https://platform.openai.com/docs/models/gpt-4o)
- [GPT-4.1 ↗](https://developers.openai.com/api/docs/models/gpt-4.1)
- [GPT-4.1 mini ↗](https://developers.openai.com/api/docs/models/gpt-4.1-mini)
- [GPT-4.1 nano ↗](https://developers.openai.com/api/docs/models/gpt-4.1-nano)
- [GPT-5 ↗](https://developers.openai.com/api/docs/models/gpt-5)
- [GPT-5 Pro ↗](https://platform.openai.com/docs/models/gpt-5-pro)
- [GPT-5 Codex ↗](https://developers.openai.com/api/docs/models/gpt-5-codex)
- [GPT-5 mini ↗](https://developers.openai.com/api/docs/models/gpt-5-mini)
- [GPT-5 nano ↗](https://developers.openai.com/api/docs/models/gpt-5-nano)
- [GPT-5.1 ↗](https://developers.openai.com/api/docs/models/gpt-5.1)
- [GPT-5.1 Codex↗](https://developers.openai.com/api/docs/models/gpt-5.1-codex)
- [GPT-5.1 Codex Mini↗](https://developers.openai.com/api/docs/models/gpt-5.1-codex-mini)
- [GPT-5.2 ↗](https://platform.openai.com/docs/models/gpt-5.2)
- [GPT-5.2 Pro ↗](https://developers.openai.com/api/docs/models/gpt-5.2-pro)
- [GPT-5.3 Codex ↗](https://platform.openai.com/docs/models/gpt-5.3-codex)
- [GPT-5.4 ↗](https://openai.com/index/introducing-gpt-5-4/)
- [GPT-5.4 Pro ↗](https://platform.openai.com/docs/models/gpt-5.4-pro)
- [GPT-5.4 mini ↗](https://openai.com/index/introducing-gpt-5-4/)
- [GPT-5.4 nano ↗](https://openai.com/index/introducing-gpt-5-4/)
- [GPT-5.5 ↗](https://openai.com/index/introducing-gpt-5-5/)
- [GPT-5.6 Luna ↗](https://platform.openai.com/docs/models/gpt-5.6-luna)
- [GPT-5.6 Sol ↗](https://platform.openai.com/docs/models/gpt-5.6-sol)
- [GPT-5.6 Terra ↗](https://platform.openai.com/docs/models/gpt-5.6-terra)
- [GPT-6 Astra ↗](https://developers.openai.com/api/docs/models/gpt-6-astra)
- [GPT-6 Luna ↗](https://developers.openai.com/api/docs/models/gpt-6-luna)
- [GPT-6 Sol ↗](https://developers.openai.com/api/docs/models/gpt-6-sol)
- [GPT-6.1 Sol (Experimental) ↗](https://developers.openai.com/api/docs/models/gpt-6.1-sol)
- [GPT-OSS-20B ↗](https://developers.openai.com/api/docs/models/gpt-oss-20b)
- [GPT-OSS-120B ↗](https://developers.openai.com/api/docs/models/gpt-oss-120b)
- [o1 ↗](https://developers.openai.com/api/docs/models/o1)
- [o3-mini ↗](https://platform.openai.com/docs/models/o3-mini)
- [o3 ↗](https://developers.openai.com/api/docs/models/o3)
- [o4-mini ↗](https://developers.openai.com/api/docs/models/o4-mini)
- [Anthropic Claude 4 Sonnet ↗](https://www.anthropic.com/claude/sonnet)
- [Anthropic Claude 4.1 Opus ↗](https://www.anthropic.com/claude/opus)
- [Claude Haiku 5.5 (Experimental) ↗](https://platform.claude.com/docs/en/models/haiku-5-5/overview)
- [Anthropic Claude 4.5 Haiku ↗](https://www.anthropic.com/claude/haiku)
- [Anthropic Claude 4.5 Sonnet ↗](https://www.anthropic.com/claude/sonnet)
- [Anthropic Claude 4.5 Opus ↗](https://www.anthropic.com/claude/opus)
- [Anthropic Claude 4.6 Sonnet ↗](https://www.anthropic.com/claude/sonnet)
- [Anthropic Claude 4.6 Opus ↗](https://www.anthropic.com/claude/opus)
- [Anthropic Claude 4.7 Opus ↗](https://www.anthropic.com/claude/opus)
- [Anthropic Claude 4.8 Opus ↗](https://www.anthropic.com/claude/opus)
- [Anthropic Claude 5 Sonnet ↗](https://www.anthropic.com/claude/sonnet)
- [Anthropic Claude 5 Opus ↗](https://www.anthropic.com/claude/opus)
- [Anthropic Claude 5.5 Sonnet (Experimental) ↗](https://www.anthropic.com/claude/sonnet)
- [Anthropic Claude 5.5 Opus ↗](https://www.anthropic.com/claude/opus)
- [Mistral Small 24B (Experimental) ↗](https://docs.mistral.ai/getting-started/models/models_overview/)
- [Llama 3.1\_8B ↗](https://www.llama.com/docs/model-cards-and-prompt-formats/llama3_1/)
- [Llama 3.3\_70B ↗](https://www.llama.com/docs/model-cards-and-prompt-formats/llama3_3/)
- [Llama 3.3 Nemotron Super 49b v1.5 ↗](https://build.nvidia.com/nvidia/llama-3_3-nemotron-super-49b-v1_5/modelcard)
- [NVIDIA Nemotron 3 Nano 30B ↗](https://build.nvidia.com/nvidia/nemotron-3-nano-30b-a3b/modelcard)
- [NVIDIA Nemotron 3 Super 120B ↗](https://build.nvidia.com/nvidia/nemotron-3-super-120b-a12b/modelcard)
- [NVIDIA Nemotron 3 Super 120B FP8 ↗](https://build.nvidia.com/nvidia/nemotron-3-super-120b-a12b/modelcard)
- [Llama 4 Scout 17b 16E Instruct ↗](https://www.llama.com/docs/model-cards-and-prompt-formats/llama4/)
- [Llama 4 Maverick 17b 128E Instruct ↗](https://www.llama.com/docs/model-cards-and-prompt-formats/llama4/)
- [Gemma 4 26B A4B ↗](https://huggingface.co/google/gemma-4-26B-A4B-it)
- [Gemma 4 31B ↗](https://huggingface.co/google/gemma-4-31B-it)
- [Gemini 2.5 Pro ↗](https://ai.google.dev/gemini-api/docs/pricing#gemini-2.5-pro)
- [Gemini 2.5 Flash ↗](https://ai.google.dev/gemini-api/docs/pricing#gemini-2.5-flash)
- [Gemini 2.5 Flash Lite ↗](https://ai.google.dev/gemini-api/docs/pricing#gemini-2.5-flash-lite)
- [Gemini 3 Flash ↗](https://ai.google.dev/gemini-api/docs/pricing#gemini-3-flash-preview)
- [Gemini 3.1 Pro ↗](https://ai.google.dev/gemini-api/docs/pricing#gemini-3-1-pro-preview)
- [Gemini 3.1 Flash-Lite ↗](https://blog.google/innovation-and-ai/models-and-research/gemini-models/gemini-3-1-flash-lite/)
- [Gemini 3.5 Flash ↗](https://ai.google.dev/gemini-api/docs/pricing#gemini-3-5-flash)
- [Gemini 3.5 Flash-Lite ↗](https://ai.google.dev/gemini-api/docs/pricing#gemini-3.5-flash-lite)
- [Gemini 3.6 Flash ↗](https://ai.google.dev/gemini-api/docs/pricing#gemini-3.6-flash)
- [Gemini 3.7 Flash ↗](https://ai.google.dev/gemini-api/docs/pricing#gemini-3.7-flash)
- [Gemini 3.8 Flash ↗](https://docs.cloud.google.com/gemini-enterprise-agent-platform/models/gemini/3-8-flash)
- [Gemini 4 Argon (Experimental) ↗](https://docs.cloud.google.com/gemini-enterprise-agent-platform/models/gemini/4-argon)
- [DeepSeek V3.2 ↗](https://huggingface.co/deepseek-ai/DeepSeek-V3.2)
- [DeepSeek V4.1 Flash ↗](https://huggingface.co/deepseek-ai/DeepSeek-V4.1-Flash)
- [Kimi K2.5 ↗](https://huggingface.co/moonshotai/Kimi-K2.5)
- [Kimi K3 ↗](https://huggingface.co/moonshotai/Kimi-K3)
- [Qwen3 235B A22B 2507 ↗](https://qwen.ai/blog?id=qwen3)
- [Qwen3 32B ↗](https://qwen.ai/blog?id=qwen3)
- [Z.ai GLM-5 ↗](https://huggingface.co/zai-org/GLM-5)
- [Z.ai GLM-5.3 ↗](https://huggingface.co/zai-org/GLM-5.3)
- [Z.ai GLM-5.3 Flash ↗](https://huggingface.co/zai-org/GLM-5.3-Flash)

## Available text embedding models

Palantir AIP also supports the following text embedding models:

- [Llama 3.2 NV EmbedQA 1B v2 ↗](https://build.nvidia.com/nvidia/llama-3_2-nv-embedqa-1b-v2)
- [`ada` embedding ↗](https://developers.openai.com/api/docs/guides/embeddings)
- [text-embedding-3-large ↗](https://developers.openai.com/api/docs/guides/embeddings)
- [text-embedding-3-small ↗](https://developers.openai.com/api/docs/guides/embeddings)
- [Snowflake Arctic Embed Medium ↗](https://www.snowflake.com/en/data-cloud/arctic/)

## Available audio models

Recording and consent are your responsibility

Before deploying any application that records or transcribes human speech, ensure participants are notified and that you have consent flows in place where required by the jurisdictions you operate in. This may apply whether the participant is the application user, a third party on a call, or anyone else whose voice is captured. See [Recording, transcription, and consent](/docs/foundry/realtime-audio/overview/#recording-transcription-and-consent).

Palantir AIP supports two categories of audio models: realtime speech-to-speech models for building conversational voice applications, and transcription models for converting audio into text. To learn how to use audio models from a browser-based application authenticated as a Foundry user, see [Build a voice-enabled OSDK application](/docs/foundry/realtime-audio/build-a-voice-enabled-osdk-application/). For a high-level overview of audio on Foundry, see [Realtime audio](/docs/foundry/realtime-audio/overview/).

The **Mode** column indicates which usage paths each model supports:

- **Realtime:** streaming bidirectional WebSocket. The model handles voice activity detection, turn-taking, and streaming output; the application sends raw audio as it is captured.
- **Static:** request-response HTTP. The application sends a bounded audio payload and receives the transcription back. Suited to transcribing previously recorded audio files. Also viable for live transcription if the application performs its own audio chunking — a trade-off that adds application complexity in exchange for lower and more predictable per-minute cost. This approach also helps when a realtime model is not available on your enrollment. Latency depends on chunk size and model, so evaluate it for your use case.

### Realtime speech-to-speech

Realtime speech-to-speech models accept streaming audio input, make tool calls during the conversation, and return streaming audio output over a WebSocket connection.

| Model | API name | Provider | Mode |
| --- | --- | --- | --- |
| [GPT Realtime 1.5 ↗](https://developers.openai.com/api/docs/models/gpt-realtime-1.5) | `gpt-realtime-1.5` | OpenAI Direct, Azure OpenAI | Realtime |
| [GPT Realtime 2.0 ↗](https://developers.openai.com/api/docs/models/gpt-realtime-2) | `gpt-realtime-2` | OpenAI Direct | Realtime |

### Transcription

Transcription models convert audio into text. Most support both realtime (streaming WebSocket) and static (request-response HTTP) modes; `Whisper_large_v3` is Palantir-hosted and supports static only.

| Model | API name | Provider | Mode |
| --- | --- | --- | --- |
| [Whisper 1 ↗](https://platform.openai.com/docs/models/whisper-1) | `whisper-1` | OpenAI Direct, Azure OpenAI | Realtime, Static |
| [GPT-4o Transcribe ↗](https://platform.openai.com/docs/models/gpt-4o-transcribe) | `gpt-4o-transcribe` | OpenAI Direct, Azure OpenAI | Realtime, Static |
| [GPT-4o Mini Transcribe ↗](https://platform.openai.com/docs/models/gpt-4o-mini-transcribe) | `gpt-4o-mini-transcribe` | OpenAI Direct, Azure OpenAI | Realtime, Static |
| [GPT-4o Transcribe Diarize ↗](https://platform.openai.com/docs/models/gpt-4o-transcribe-diarize) | `gpt-4o-transcribe-diarize` | OpenAI Direct, Azure OpenAI | Realtime, Static |
| [Whisper Large V3 ↗](https://huggingface.co/openai/whisper-large-v3) | `Whisper_large_v3` | Palantir-hosted | Static |

Use `gpt-4o-transcribe-diarize` when you need speaker diarization for multi-speaker audio such as meetings. For general-purpose transcription where diarization is not required, use `gpt-4o-transcribe`, `gpt-4o-mini-transcribe`, or `whisper-1` depending on the quality, cost, and latency trade-offs that fit your use case. Use `Whisper_large_v3` in environments where OpenAI Direct or Azure OpenAI integrations are not available.

For per-enrollment availability across geographic regions, see the [LLM availability by geography](#llm-availability-by-geography) table below.

## LLM availability prerequisites

AIP is model-agnostic and supports a diverse selection of models for LLM-powered use cases; for more information, refer to [this list of all models available in AIP](#llm-availability-by-geography).

However, LLM selection and availability differs across enrollments based on certain prerequisites for a specific model to be available on an enrollment. These prerequisites determine whether a model family appears as **enabled**, **disabled**, or **disallowed** in Control Panel. Learn more about [model states in the Model enablement interface](/docs/foundry/aip/enable-aip-features/#understanding-model-states).

The criteria are listed below:

- **Model has been integrated with AIP:** Palantir aims to support the latest flagship models in the industry and is actively developing support in line with model releases and updates.
- **Legal acknowledgment has been given where required:** Enrollment administrators must accept the legal requirements and terms of use of a provider to have certain models enabled. This can usually be done in Control Panel under the **Model enablement** tab. Models awaiting legal acknowledgment appear in a **disabled** state.
- **AIP has been enabled on an enrollment to use an LLM:** For usage in products such as AIP Logic, Transforms, Functions, and Pipeline Builder, the permissions for AIP capabilities for custom workflows must be enabled for intended user groups.
- **Regional availability compatibility (for external provider models):** For models like GPT, Claude, and Gemini, you may need to consider regional availability if you are on a geography-restricted enrollment. For example, GPT4o and Claude 3 Sonnet were both only available in the US on release from each respective model provider before they were available in the EU, UK, and other regions. Review the [model georestriction section](#llm-availability-by-geography) for details. Models unavailable due to geographical restrictions appear in a **disallowed** state.
- **Additional reviews have been conducted (for certain Palantir-provided models):** Open-source models, such as Llama and Mixtral, may require an additional Palantir engineering review to support your environment. Models requiring these reviews appear in a **disallowed** state.
- **Sufficient time to integration with a specific AIP frontend product:** New LLMs take time to be fully supported on all AIP products (for example, in AIP Logic and in Pipeline Builder's use LLM node feature).
- **Risk consideration (for experimental models):** Experimental models may change or require manual migration. Access is limited and may require acknowledgment before use. Experimental status may reflect the model provider or the model's integration with AIP. Experimental models are not intended for production use.

## LLM rate limits

[For information on LLM rate limits, review the documentation on LLM capacity management.](/docs/foundry/aip/llm-capacity-management/)

### LLM availability by geography

Geographical restrictions, or georestrictions, limit model availability by requiring LLM requests to stay within a specific region. For example, if an enrollment is defined as EU geo-restricted, all LLM requests will be processed in the EU. Non-georestricted enrollments are also subject to the [model availability prerequisites](#llm-availability-prerequisites).

The following table indicates the regional georestriction options and US Government impact levels for the various models supported by AIP. Regional georestriction refers to the enrollment setup, not to the location of a specific user. The country and region columns describe commercial georestrictions; IL2, IL4, and IL5 describe government availability. A check mark indicates support for that model and provider in the specified environment. A blank cell indicates that the option is unavailable; a model with no regional check marks may still be available on non-georestricted commercial enrollments.

For Palantir-hosted models, availability in South Korea (KR) depends on your environment and is not listed in the table below. The KR column shows availability through other providers; blank KR cells for Palantir-hosted models do not indicate that those models are unavailable. Contact Palantir Support to confirm availability for your environment.

Rows marked **Experimental** identify experimental offerings for that provider. Access is subject to the [experimental model prerequisites](#llm-availability-prerequisites).

Availability is specific to the model and provider. For example, GPT-5.6 Luna and GPT-5.6 Terra are available through Azure on IL2, IL4, and IL5 enrollments, and through Amazon Bedrock on IL4 and IL5 enrollments. GPT-6 Luna is available through Azure, Amazon Bedrock, and OpenAI on commercial enrollments, subject to the regional restrictions below. The Amazon Bedrock offering uses global inference and requires a non-georestricted enrollment.

| Model Provider | Model | US | EU | UK | CA | AU | JP | KR | KSA | IL2 | IL4 | IL5 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Anthropic | Claude Haiku 4.5 | ✅ |  |  |  |  |  |  |  |  |  |  |
| Amazon Bedrock | Claude Haiku 4.5 | ✅ | ✅ |  |  | ✅ | ✅ |  |  |  |  |  |
| Azure | Claude Haiku 4.5 |  |  |  |  |  |  |  |  |  |  |  |
| Google Vertex | Claude Haiku 4.5 | ✅ | ✅ |  |  |  |  |  |  | ✅ | ✅ |  |
| Anthropic | Claude Haiku 5.5 (Experimental) | ✅ |  |  |  |  |  |  |  |  |  |  |
| Amazon Bedrock | Claude Haiku 5.5 (Experimental) | ✅ | ✅ |  |  | ✅ | ✅ |  |  |  |  |  |
| Azure | Claude Haiku 5.5 (Experimental) | ✅ |  |  |  |  |  |  |  |  |  |  |
| Google Vertex | Claude Haiku 5.5 (Experimental) | ✅ | ✅ |  |  |  |  |  |  | ✅ | ✅ |  |
| Amazon Bedrock | Claude Opus 4.1 | ✅ |  |  |  |  |  |  |  |  |  |  |
| Anthropic | Claude Opus 4.5 | ✅ |  |  |  |  |  |  |  |  |  |  |
| Amazon Bedrock | Claude Opus 4.5 | ✅ | ✅ |  |  |  |  |  |  |  |  |  |
| Azure | Claude Opus 4.5 |  |  |  |  |  |  |  |  |  |  |  |
| Google Vertex | Claude Opus 4.5 | ✅ | ✅ |  |  |  |  |  |  | ✅ | ✅ |  |
| Anthropic | Claude Opus 4.6 | ✅ |  |  |  |  |  |  |  |  |  |  |
| Amazon Bedrock | Claude Opus 4.6 | ✅ | ✅ | ✅ |  |  |  |  |  |  |  |  |
| Azure | Claude Opus 4.6 |  |  |  |  |  |  |  |  |  |  |  |
| Google Vertex | Claude Opus 4.6 | ✅ | ✅ |  |  |  |  |  |  | ✅ | ✅ |  |
| Anthropic | Claude Opus 4.7 | ✅ |  |  |  |  |  |  |  |  |  |  |
| Amazon Bedrock | Claude Opus 4.7 | ✅ | ✅ |  |  |  | ✅ |  |  |  |  |  |
| Azure | Claude Opus 4.7 |  |  |  |  |  |  |  |  |  |  |  |
| Google Vertex | Claude Opus 4.7 | ✅ | ✅ |  |  |  |  |  |  | ✅ | ✅ |  |
| Anthropic | Claude Opus 4.8 | ✅ |  |  |  |  |  |  |  |  |  |  |
| Amazon Bedrock | Claude Opus 4.8 | ✅ | ✅ |  |  |  | ✅ |  |  | ✅ | ✅ |  |
| Azure | Claude Opus 4.8 | ✅ |  |  |  |  |  |  |  |  |  |  |
| Google Vertex | Claude Opus 4.8 | ✅ | ✅ |  |  |  |  |  |  | ✅ | ✅ |  |
| Anthropic | Claude Opus 5 | ✅ |  |  |  |  |  |  |  |  |  |  |
| Amazon Bedrock | Claude Opus 5 | ✅ | ✅ |  |  | ✅ |  | ✅ |  | ✅ | ✅ |  |
| Azure | Claude Opus 5 | ✅ |  |  |  |  |  |  |  |  |  |  |
| Google Vertex | Claude Opus 5 | ✅ | ✅ |  |  |  |  |  |  | ✅ | ✅ |  |
| Anthropic | Claude Opus 5.5 | ✅ |  |  |  |  |  |  |  |  |  |  |
| Amazon Bedrock | Claude Opus 5.5 | ✅ | ✅ | ✅ |  | ✅ |  |  |  | ✅ | ✅ |  |
| Azure | Claude Opus 5.5 | ✅ |  |  |  |  |  |  |  |  |  |  |
| Google Vertex | Claude Opus 5.5 | ✅ | ✅ |  |  |  |  |  |  | ✅ | ✅ |  |
| Google Vertex | Claude Sonnet 4 |  |  |  |  |  |  |  |  | ✅ |  |  |
| Anthropic | Claude Sonnet 4.5 | ✅ |  |  |  |  |  |  |  |  |  |  |
| Amazon Bedrock | Claude Sonnet 4.5 | ✅ | ✅ |  |  |  | ✅ |  |  |  |  |  |
| Azure | Claude Sonnet 4.5 |  |  |  |  |  |  |  |  |  |  |  |
| Google Vertex | Claude Sonnet 4.5 | ✅ | ✅ |  |  |  |  |  |  | ✅ | ✅ |  |
| Anthropic | Claude Sonnet 4.6 | ✅ |  |  |  |  |  |  |  |  |  |  |
| Amazon Bedrock | Claude Sonnet 4.6 | ✅ | ✅ | ✅ |  | ✅ | ✅ |  |  |  |  |  |
| Azure | Claude Sonnet 4.6 |  |  |  |  |  |  |  |  |  |  |  |
| Google Vertex | Claude Sonnet 4.6 | ✅ | ✅ |  |  |  |  |  |  | ✅ | ✅ |  |
| Anthropic | Claude Sonnet 5 | ✅ |  |  |  |  |  |  |  |  |  |  |
| Amazon Bedrock | Claude Sonnet 5 | ✅ |  | ✅ |  |  |  | ✅ |  | ✅ | ✅ |  |
| Azure | Claude Sonnet 5 | ✅ |  |  |  |  |  |  |  |  |  |  |
| Google Vertex | Claude Sonnet 5 | ✅ | ✅ |  |  |  |  |  |  | ✅ | ✅ |  |
| Anthropic | Claude Sonnet 5.5 (Experimental) | ✅ |  |  |  |  |  |  |  |  |  |  |
| Amazon Bedrock | Claude Sonnet 5.5 (Experimental) | ✅ | ✅ |  |  |  |  |  |  | ✅ | ✅ |  |
| Azure | Claude Sonnet 5.5 (Experimental) | ✅ |  |  |  |  |  |  |  |  |  |  |
| Google Vertex | Claude Sonnet 5.5 (Experimental) | ✅ | ✅ |  |  |  |  |  |  | ✅ | ✅ |  |
| Google Vertex | Gemini 3 Flash (Preview) | ✅ |  |  |  |  |  |  |  | ✅ | ✅ | ✅ |
| Google Vertex | Gemini 3.1 Flash Lite | ✅ | ✅ |  |  |  |  |  |  | ✅ | ✅ | ✅ |
| Google Vertex | Gemini 3.1 Pro (Preview) | ✅ |  |  |  |  |  |  |  | ✅ | ✅ | ✅ |
| Google Vertex | Gemini 3.5 Flash | ✅ | ✅ |  |  |  |  |  |  | ✅ | ✅ | ✅ |
| Google Vertex | Gemini 3.5 Flash-Lite | ✅ | ✅ |  |  |  |  |  |  | ✅ | ✅ | ✅ |
| Google Vertex | Gemini 3.6 Flash | ✅ | ✅ |  |  |  |  |  |  | ✅ | ✅ | ✅ |
| Google Vertex | Gemini 3.7 Flash | ✅ | ✅ |  |  |  |  |  |  | ✅ | ✅ | ✅ |
| Google Vertex | Gemini 3.8 Flash | ✅ | ✅ |  |  |  |  |  |  | ✅ | ✅ | ✅ |
| Google Vertex | Gemini 4 Argon (Experimental) | ✅ | ✅ |  |  |  |  |  |  | ✅ | ✅ | ✅ |
| Amazon Bedrock | DeepSeek V3.2 | ✅ | ✅ | ✅ |  | ✅ | ✅ |  |  |  |  |  |
| Amazon Bedrock | Kimi K2.5 | ✅ | ✅ | ✅ |  | ✅ | ✅ |  |  |  |  |  |
| Amazon Bedrock | Kimi K3 | ✅ |  |  |  |  |  |  |  |  |  |  |
| Amazon Bedrock | Qwen3 235B A22B 2507 | ✅ | ✅ | ✅ |  | ✅ | ✅ |  |  |  |  |  |
| Amazon Bedrock | Qwen3 32B | ✅ | ✅ | ✅ |  | ✅ | ✅ |  |  |  |  |  |
| Amazon Bedrock | Z.ai GLM-5 | ✅ | ✅ | ✅ |  | ✅ | ✅ |  |  |  |  |  |
| Amazon Bedrock | Z.ai GLM-5.3 | ✅ |  |  |  |  |  |  |  |  |  |  |
| Amazon Bedrock | Gemma 4 26B A4B | ✅ | ✅ |  |  |  |  |  |  |  |  |  |
| Amazon Bedrock | Gemma 4 31B | ✅ | ✅ |  |  |  |  |  |  |  |  |  |
| Amazon Bedrock | Llama 3.1 8b Instruct | ✅ |  |  |  |  |  |  |  |  |  |  |
| Palantir-hosted | Llama 3.1 8b Instruct |  |  |  |  |  |  |  |  |  |  |  |
| Amazon Bedrock | Llama 3.3 70b Instruct | ✅ |  |  |  |  |  |  |  |  |  |  |
| Palantir-hosted | Llama 3.3 70b Instruct |  |  |  |  |  |  |  |  |  |  |  |
| Amazon Bedrock | Llama 4 Maverick 17b 128E Instruct | ✅ |  |  |  |  |  |  |  |  |  |  |
| Amazon Bedrock | Llama 4 Scout 17b 16E Instruct | ✅ |  |  |  |  |  |  |  |  |  |  |
| Palantir-hosted | Llama 4 Scout 17b 16E Instruct |  |  |  |  |  |  |  |  |  |  |  |
| Palantir-hosted | Llama 3.2 NV EmbedQA 1B v2 |  |  |  |  |  |  |  |  |  |  |  |
| Palantir-hosted | Llama 3.3 Nemotron Super 49b v1.5 |  |  |  |  |  |  |  |  |  |  |  |
| Amazon Bedrock | NVIDIA Nemotron 3 Nano 30B | ✅ | ✅ | ✅ |  | ✅ | ✅ |  |  |  |  |  |
| Amazon Bedrock | NVIDIA Nemotron 3 Super 120B | ✅ | ✅ | ✅ |  | ✅ | ✅ |  |  |  |  |  |
| Azure | GPT-4.1 | ✅ | ✅ |  |  |  |  |  |  | ✅ | ✅ | ✅ |
| OpenAI | GPT-4.1 | ✅ |  |  |  |  |  |  |  |  |  |  |
| Azure | GPT-4.1 mini | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |  |  | ✅ | ✅ | ✅ |
| OpenAI | GPT-4.1 mini | ✅ |  |  |  |  |  |  |  |  |  |  |
| Azure | GPT-4.1 nano | ✅ | ✅ |  |  |  |  |  |  |  |  |  |
| Azure | GPT-4o | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |  |  | ✅ | ✅ | ✅ |
| OpenAI | GPT-4o | ✅ |  |  |  |  |  |  |  |  |  |  |
| Azure | GPT-5 | ✅ | ✅ |  |  |  |  |  |  |  |  |  |
| Azure | GPT-5 Codex |  |  |  |  |  |  |  |  |  |  |  |
| Azure | GPT-5 mini | ✅ | ✅ |  |  |  |  |  |  |  |  |  |
| Azure | GPT-5 nano | ✅ | ✅ |  |  |  |  |  |  |  |  |  |
| Azure | GPT-5.1 | ✅ | ✅ | ✅ |  |  |  |  |  | ✅ | ✅ | ✅ |
| OpenAI | GPT-5.1 | ✅ |  |  |  |  |  |  |  |  |  |  |
| Azure | GPT-5.1 Codex |  |  |  |  |  |  |  |  |  |  |  |
| Azure | GPT-5.1 Codex mini |  |  |  |  |  |  |  |  |  |  |  |
| Azure | GPT-5.2 | ✅ |  |  |  |  |  |  |  |  |  |  |
| OpenAI | GPT-5.2 | ✅ |  |  |  |  |  |  |  |  |  |  |
| OpenAI | GPT-5.2 Pro | ✅ |  |  |  |  |  |  |  |  |  |  |
| Azure | GPT-5.3 Codex | ✅ |  |  |  |  |  |  |  |  |  |  |
| OpenAI | GPT-5.3 Codex |  |  |  |  |  |  |  |  |  |  |  |
| Azure | GPT-5.4 | ✅ | ✅ |  |  |  |  |  |  |  |  |  |
| Amazon Bedrock | GPT-5.4 | ✅ |  |  |  |  |  |  |  |  | ✅ | ✅ |
| OpenAI | GPT-5.4 | ✅ |  |  |  |  |  |  |  |  |  |  |
| OpenAI | GPT-5.4 Pro | ✅ |  |  |  |  |  |  |  |  |  |  |
| Azure | GPT-5.4 mini | ✅ |  |  |  |  |  |  |  |  |  |  |
| OpenAI | GPT-5.4 mini | ✅ |  |  |  |  |  |  |  |  |  |  |
| Azure | GPT-5.4 nano | ✅ |  |  |  |  |  |  |  |  |  |  |
| OpenAI | GPT-5.4 nano | ✅ |  |  |  |  |  |  |  |  |  |  |
| Azure | GPT-5.5 | ✅ | ✅ |  |  |  |  |  |  |  |  |  |
| Amazon Bedrock | GPT-5.5 | ✅ |  |  |  |  |  |  |  |  |  |  |
| OpenAI | GPT-5.5 | ✅ |  |  |  |  |  |  |  |  |  |  |
| Azure | GPT-5.6 Luna | ✅ | ✅ |  |  |  |  |  |  | ✅ | ✅ | ✅ |
| Amazon Bedrock | GPT-5.6 Luna | ✅ |  |  |  |  |  |  |  |  | ✅ | ✅ |
| OpenAI | GPT-5.6 Luna | ✅ |  |  |  |  |  |  |  |  |  |  |
| Azure | GPT-5.6 Sol | ✅ | ✅ |  |  |  |  |  |  | ✅ | ✅ | ✅ |
| OpenAI | GPT-5.6 Sol | ✅ |  |  |  |  |  |  |  |  |  |  |
| Azure | GPT-5.6 Terra | ✅ | ✅ |  |  |  |  |  |  | ✅ | ✅ | ✅ |
| Amazon Bedrock | GPT-5.6 Terra | ✅ |  |  |  |  |  |  |  |  | ✅ | ✅ |
| OpenAI | GPT-5.6 Terra | ✅ |  |  |  |  |  |  |  |  |  |  |
| Azure | GPT-6 Astra | ✅ |  |  |  |  |  |  |  |  |  |  |
| Amazon Bedrock | GPT-6 Astra | ✅ |  |  |  |  |  |  |  |  |  |  |
| OpenAI | GPT-6 Astra | ✅ |  |  |  |  |  |  |  |  |  |  |
| Azure | GPT-6 Luna | ✅ | ✅ |  |  |  |  |  |  |  |  |  |
| Amazon Bedrock | GPT-6 Luna |  |  |  |  |  |  |  |  |  |  |  |
| OpenAI | GPT-6 Luna | ✅ |  |  |  |  |  |  |  |  |  |  |
| Azure | GPT-6 Sol | ✅ | ✅ |  |  |  |  |  |  |  |  |  |
| Amazon Bedrock | GPT-6 Sol |  |  |  |  |  |  |  |  |  |  |  |
| OpenAI | GPT-6 Sol | ✅ |  |  |  |  |  |  |  |  |  |  |
| Azure | GPT-6.1 Sol (Experimental) |  |  |  |  |  |  |  |  |  |  |  |
| Amazon Bedrock | GPT-6.1 Sol (Experimental) | ✅ |  |  |  |  |  |  |  |  |  |  |
| OpenAI | GPT-6.1 Sol (Experimental) | ✅ |  |  |  |  |  |  |  |  |  |  |
| Palantir-hosted | GPT-OSS-120B | ✅ |  |  |  |  |  |  |  | ✅ | ✅ | ✅ |
| Palantir-hosted | GPT-OSS-20B | ✅ |  |  |  |  |  |  |  | ✅ | ✅ | ✅ |
| Azure | Text Embedding 3 Large | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |  | ✅ | ✅ | ✅ |
| OpenAI | Text Embedding 3 Large | ✅ |  |  |  |  |  |  |  |  |  |  |
| Azure | Text Embedding 3 Small | ✅ | ✅ |  | ✅ | ✅ | ✅ |  |  | ✅ | ✅ | ✅ |
| OpenAI | Text Embedding 3 Small | ✅ |  |  |  |  |  |  |  |  |  |  |
| OpenAI | Whisper 1 | ✅ |  |  |  |  |  |  |  |  |  |  |
| Azure | o1 | ✅ | ✅ |  |  |  |  |  |  |  |  |  |
| Azure | o3 | ✅ | ✅ |  |  |  |  |  |  |  |  |  |
| Azure | o3-mini | ✅ | ✅ |  |  |  |  |  |  | ✅ | ✅ | ✅ |
| Azure | o4-mini | ✅ | ✅ |  |  |  |  |  |  |  |  |  |
| Azure | text-embedding-ada-002 | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |  |  | ✅ | ✅ | ✅ |
| OpenAI | text-embedding-ada-002 | ✅ |  |  |  |  |  |  |  |  |  |  |
| Palantir-hosted | Document Information Extraction | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |  |  | ✅ | ✅ | ✅ |
| Palantir-hosted | Gemma 4 26B A4B | ✅ |  |  |  |  |  |  |  | ✅ | ✅ | ✅ |
| Palantir-hosted | Schematic 7B |  |  |  |  |  |  |  |  | ✅ | ✅ | ✅ |
| Palantir-hosted | Whisper Large V3 | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |  |  | ✅ | ✅ | ✅ |
| Palantir-hosted | Snowflake Arctic Embed Medium |  |  |  |  |  |  |  |  |  |  |  |
| xAI | Grok 4.3 | ✅ |  |  |  |  |  |  |  |  |  |  |
| xAI | Grok 4.5 | ✅ |  |  |  |  |  |  |  |  |  |  |
| xAI | Grok 4.6 | ✅ |  |  |  |  |  |  |  |  |  |  |
| xAI | Grok 4.7 | ✅ |  |  |  |  |  |  |  |  |  |  |
| xAI | Grok 420 Non-Reasoning Latest | ✅ |  |  |  |  |  |  |  |  |  |  |
| xAI | Grok 420 Reasoning Latest | ✅ |  |  |  |  |  |  |  |  |  |  |
| xAI | Grok Build 0.1 | ✅ |  |  |  |  |  |  |  |  |  |  |
| Fireworks | DeepSeek V4.1 Flash |  |  |  |  |  |  |  |  |  |  |  |
| Fireworks | Kimi K3 |  |  |  |  |  |  |  |  |  |  |  |
| Fireworks | Z.ai GLM-5.3 |  |  |  |  |  |  |  |  |  |  |  |
| Fireworks | Z.ai GLM-5.3 Flash |  |  |  |  |  |  |  |  |  |  |  |

## Bring your own model (LLM)

Bring your own model is a capability that provides first-class support for customers that would like to connect their own LLMs or accounts to use in AIP with all Palantir developer products - AIP Logic, Pipeline Builder, Chatbot Studio, Workshop, etc.

Review the [bring your own model](/docs/foundry/aip/bring-your-own-model/) documentation to learn how to register models for use in AIP.

---

Note: AIP feature availability is subject to change and may differ between customers.

*The "OpenAI" name and the “GPT” brands are property of OpenAI.*

All third-party trademarks (including logos and icons) referenced remain the property of their respective owners. No affiliation or endorsement is implied.
