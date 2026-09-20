"""Project Spider - a project-local crawler that builds verified datasets.

`spider init` once per project, then describe the dataset you want in
`spider.yaml`, collect pages, and build a normalised database where every
value carries its source, its evidence quote and a confidence score.
"""

__version__ = "0.1.0"
import os

# How Spider introduces itself. It used to include a link to a repository that
# does not exist - a made-up contact in every request. It now says only what is
# true, and a project that wants a real contact adds one:
#   SPIDER_USER_AGENT="ProjectSpider/0.1 (+mailto:you@example.org)"
USER_AGENT = os.environ.get(
    "SPIDER_USER_AGENT", f"ProjectSpider/{__version__} (polite project-local crawler)")
