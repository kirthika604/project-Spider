"""Project Spider - a project-local crawler that builds verified datasets.

`spider init` once per project, then describe the dataset you want in
`spider.yaml`, collect pages, and build a normalised database where every
value carries its source, its evidence quote and a confidence score.
"""

__version__ = "0.1.0"
USER_AGENT = (
    "ProjectSpider/0.1 (+https://github.com/project-spider; "
    "polite project-local crawler; contact: see spider.yaml)"
)
