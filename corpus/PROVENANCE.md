# Where the corpus comes from

Every case below is derived from the root `.gitignore` of a real repository, and every verdict is git's own, taken from a real path in a real working tree.

| repository | .gitignore lines | paths asked | cases kept | dropped | sha256 |
|---|---:|---:|---:|---:|---|
| [`angular/angular`](https://raw.githubusercontent.com/angular/angular/main/.gitignore) | 60 | 213 | 265 | 0 | `c5f73d7b67ec3757` |
| [`ansible/ansible`](https://raw.githubusercontent.com/ansible/ansible/master/.gitignore) | 134 | 260 | 316 | 0 | `c1e75b1ef37af1e9` |
| [`anthropics/anthropic-sdk-python`](https://raw.githubusercontent.com/anthropics/anthropic-sdk-python/main/.gitignore) | 16 | 69 | 72 | 0 | `e291106b5b39b3ec` |
| [`apache/airflow`](https://raw.githubusercontent.com/apache/airflow/main/.gitignore) | 352 | 260 | 314 | 0 | `f6df482fe3789fe0` |
| [`cpburnz/python-pathspec`](https://raw.githubusercontent.com/cpburnz/python-pathspec/master/.gitignore) | 19 | 55 | 58 | 0 | `ae77edf9418eae3f` |
| [`denoland/deno`](https://raw.githubusercontent.com/denoland/deno/main/.gitignore) | 54 | 190 | 249 | 0 | `9fa4f53d69f2301c` |
| [`django/django`](https://raw.githubusercontent.com/django/django/main/.gitignore) | 20 | 80 | 137 | 0 | `e6ae3e0b85a5f2be` |
| [`docker/compose`](https://raw.githubusercontent.com/docker/compose/main/.gitignore) | 7 | 34 | 55 | 0 | `4fb678d3589ec2a8` |
| [`elastic/elasticsearch`](https://raw.githubusercontent.com/elastic/elasticsearch/main/.gitignore) | 102 | 260 | 320 | 0 | `e5ae4596a820e69c` |
| [`electron/electron`](https://raw.githubusercontent.com/electron/electron/main/.gitignore) | 58 | 226 | 280 | 0 | `1e140357c07d9ae5` |
| [`expressjs/express`](https://raw.githubusercontent.com/expressjs/express/master/.gitignore) | 21 | 64 | 69 | 0 | `478beef9d5dcd666` |
| [`facebook/react`](https://raw.githubusercontent.com/facebook/react/main/.gitignore) | 45 | 236 | 289 | 0 | `85340f04e1df0da3` |
| [`golang/go`](https://raw.githubusercontent.com/golang/go/master/.gitignore) | 54 | 260 | 320 | 0 | `0294a327bea72a50` |
| [`gradio-app/gradio`](https://raw.githubusercontent.com/gradio-app/gradio/main/.gitignore) | 111 | 260 | 319 | 0 | `f7736fcd66705c9a` |
| [`grafana/grafana`](https://raw.githubusercontent.com/grafana/grafana/main/.gitignore) | 298 | 260 | 316 | 0 | `e34e803bdfb8095f` |
| [`hashicorp/terraform`](https://raw.githubusercontent.com/hashicorp/terraform/main/.gitignore) | 30 | 121 | 161 | 0 | `55817ff997dc99d0` |
| [`home-assistant/core`](https://raw.githubusercontent.com/home-assistant/core/master/.gitignore) | 152 | 260 | 320 | 0 | `8632d4e62630270f` |
| [`huggingface/transformers`](https://raw.githubusercontent.com/huggingface/transformers/main/.gitignore) | 187 | 260 | 320 | 0 | `9e522ae7e6162520` |
| [`kirill-markin/repo-to-text`](https://raw.githubusercontent.com/kirill-markin/repo-to-text/main/.gitignore) | 173 | 260 | 320 | 0 | `f54a7add359d14db` |
| [`kubernetes/kubernetes`](https://raw.githubusercontent.com/kubernetes/kubernetes/master/.gitignore) | 130 | 260 | 319 | 0 | `349c333958bf246b` |
| [`langchain-ai/langchain`](https://raw.githubusercontent.com/langchain-ai/langchain/master/.gitignore) | 198 | 260 | 320 | 0 | `459b48e87ffea8eb` |
| [`microsoft/vscode`](https://raw.githubusercontent.com/microsoft/vscode/main/.gitignore) | 57 | 260 | 317 | 0 | `8c3b24d28bc386ad` |
| [`nodejs/node`](https://raw.githubusercontent.com/nodejs/node/main/.gitignore) | 184 | 260 | 319 | 0 | `b38eddd9f1de7238` |
| [`numpy/numpy`](https://raw.githubusercontent.com/numpy/numpy/main/.gitignore) | 208 | 260 | 320 | 0 | `7cd8b6553cd60722` |
| [`obsidianmd/obsidian-releases`](https://raw.githubusercontent.com/obsidianmd/obsidian-releases/master/.gitignore) | 4 | 24 | 32 | 0 | `3b13fe522dc866de` |
| [`ollama/ollama`](https://raw.githubusercontent.com/ollama/ollama/main/.gitignore) | 19 | 106 | 120 | 0 | `56d60722bd51c856` |
| [`openai/openai-python`](https://raw.githubusercontent.com/openai/openai-python/main/.gitignore) | 24 | 104 | 119 | 0 | `c0df1b11c3834f8d` |
| [`pallets/flask`](https://raw.githubusercontent.com/pallets/flask/main/.gitignore) | 9 | 42 | 83 | 0 | `a23e3badc7c5092f` |
| [`pandas-dev/pandas`](https://raw.githubusercontent.com/pandas-dev/pandas/main/.gitignore) | 142 | 260 | 320 | 0 | `1e1b9d7a523ef046` |
| [`prometheus/prometheus`](https://raw.githubusercontent.com/prometheus/prometheus/main/.gitignore) | 36 | 149 | 171 | 0 | `0966e1ec30002988` |
| [`psf/black`](https://raw.githubusercontent.com/psf/black/main/.gitignore) | 30 | 146 | 190 | 0 | `28370be46174f19c` |
| [`python/cpython`](https://raw.githubusercontent.com/python/cpython/main/.gitignore) | 188 | 260 | 320 | 0 | `8d666599c43820a4` |
| [`pytorch/pytorch`](https://raw.githubusercontent.com/pytorch/pytorch/main/.gitignore) | 413 | 260 | 319 | 0 | `43d0a93312df60af` |
| [`rust-lang/rust`](https://raw.githubusercontent.com/rust-lang/rust/main/.gitignore) | 107 | 260 | 319 | 0 | `d0a1189c37c31bf1` |
| [`scikit-learn/scikit-learn`](https://raw.githubusercontent.com/scikit-learn/scikit-learn/main/.gitignore) | 97 | 260 | 320 | 0 | `f2f695364e30ecfd` |
| [`simonw/files-to-prompt`](https://raw.githubusercontent.com/simonw/files-to-prompt/main/.gitignore) | 11 | 54 | 67 | 0 | `b4282ea105be9558` |
| [`streamlit/streamlit`](https://raw.githubusercontent.com/streamlit/streamlit/master/.gitignore) | 107 | 260 | 319 | 0 | `c9f2ef3006e0b1ca` |
| [`supabase/supabase`](https://raw.githubusercontent.com/supabase/supabase/master/.gitignore) | 169 | 260 | 307 | 0 | `86374b25f3d92709` |
| [`sveltejs/svelte`](https://raw.githubusercontent.com/sveltejs/svelte/main/.gitignore) | 31 | 86 | 110 | 0 | `8fa84ca917efd211` |
| [`tailwindlabs/tailwindcss`](https://raw.githubusercontent.com/tailwindlabs/tailwindcss/main/.gitignore) | 11 | 52 | 103 | 0 | `f68a205508bca9f3` |
| [`tensorflow/tensorflow`](https://raw.githubusercontent.com/tensorflow/tensorflow/master/.gitignore) | 55 | 260 | 318 | 0 | `ce98c5f263709997` |
| [`vercel/next.js`](https://raw.githubusercontent.com/vercel/next.js/main/.gitignore) | 40 | 129 | 136 | 0 | `d75e5552285a79bb` |
| [`vuejs/core`](https://raw.githubusercontent.com/vuejs/core/main/.gitignore) | 14 | 79 | 84 | 0 | `efbe102ae5c51b05` |

The sha256 is of the `.gitignore` text as fetched. If upstream edits their file the hash stops matching and the corpus needs rebuilding -- that is the point of recording it.
