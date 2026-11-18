# SPDX-License-Identifier: Apache-2.0
"""Inspect real target/draft config differences without loading model weights.

The width calculation uses this model's 128-channel BF16 raw key plus three
int64 positions when cache_rope_positions is enabled. Actual owners must be
confirmed in a model run; this probe alone does not allocate their KV caches.
"""

import argparse
import json

from vllm.engine.arg_utils import EngineArgs


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", required=True)
    args = parser.parse_args()
    for language_only in [True, False]:
        for mtp in [True, False]:
            kwargs = dict(
                model=args.model,
                trust_remote_code=True,
                tensor_parallel_size=4,
                data_parallel_size=2,
                enable_expert_parallel=True,
                max_model_len=143360,
                language_model_only=language_only,
                enforce_eager=True,
            )
            if mtp:
                kwargs["speculative_config"] = {
                    "method": "qwen3_5_mtp",
                    "num_speculative_tokens": 3,
                    "enforce_eager": True,
                }
            config = EngineArgs(**kwargs).create_engine_config()
            models = [("target", config.model_config)]
            if config.speculative_config is not None:
                models.append(("draft", config.speculative_config.draft_model_config))
            print(
                json.dumps(
                    {
                        "language_model_only": language_only,
                        "mtp3": mtp,
                        "configs": [
                            {
                                "role": role,
                                "uses_mrope": model.uses_mrope,
                                "predicted_raw_storage_width": 128 + (12 if model.uses_mrope else 0),
                                "rope_parameters": getattr(model.hf_text_config, "rope_parameters", None),
                            }
                            for role, model in models
                        ],
                    }
                ),
                flush=True,
            )


if __name__ == "__main__":
    main()
