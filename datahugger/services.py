import io
import re
import xml.etree.ElementTree as ET
import zipfile
from fnmatch import fnmatch
from pathlib import Path
from typing import Union
from urllib.parse import quote
from urllib.parse import urlparse

import requests
from jsonpath_ng.jsonpath import Fields
from jsonpath_ng.jsonpath import Slice

from datahugger.base import DatasetDownloader
from datahugger.utils import _get_url


class ArXivDataset(DatasetDownloader):
    """Downloader for ArXiv publication."""

    REGEXP_ID = r"https://arxiv\.org/abs/(?P<record_id>.*)"

    @property
    def files(self):
        return [
            {
                "link": f"https://arxiv.org/pdf/{self._params['record_id']}.pdf",
                "name": self._params["record_id"].split("/")[-1] + ".pdf",
                "size": None,
                "hash": None,
                "hash_type": None,
            }
        ]


class DataverseDataset(DatasetDownloader):
    """Downloader for Dataverse repository."""

    REGEXP_ID = r"(?P<type>dataset|file)\.xhtml\?persistentId=(?P<record_id>.*)"

    # paths to file attributes
    ATTR_NAME_JSONPATH = "filename"
    ATTR_SIZE_JSONPATH = "filesize"
    ATTR_HASH_JSONPATH = "md5"
    ATTR_HASH_TYPE_VALUE = "md5"

    @property
    def API_URL_META(self):
        if self._params.get("version", None):
            v = self._params["version"]
        else:
            v = ":latest-published"

        if self._params.get("type", None) == "file":
            return "{base_url}/api/files/:persistentId/?persistentId={record_id}"
        else:
            return (
                "{base_url}/api/datasets/:persistentId/versions/"
                f"{v}/?persistentId={{record_id}}"
            )

    @property
    def META_FILES_JSONPATH(self):
        if self._params.get("type", None) == "file":
            return "data.dataFile"
        else:
            return "data.files[*].dataFile"

    def _get_attr_link(self, record, base_url=None):
        return f"{base_url}/api/access/datafile/{record['id']}"


class DataDryadDataset(DatasetDownloader):
    """Downloader for DataDryad repository."""

    REGEXP_ID = r"datadryad\.org[\:]*[43]{0,3}\/stash\/dataset\/doi:(?P<record_id>.*)"

    # the base entry point of the REST API
    API_URL = "https://datadryad.org/api/v2"

    # the files and metadata about the dataset
    META_FILES_JSONPATH = (
        Fields("_embedded").child(Fields("stash:files")).child(Slice())
    )

    # paths to file attributes
    ATTR_NAME_JSONPATH = "path"
    ATTR_SIZE_JSONPATH = "size"

    @property
    def API_URL_META(self):
        doi_safe = quote(f"doi:{self._params['record_id']}", safe="")
        dataset_metadata_url = self.API_URL + "/datasets/" + doi_safe

        res = requests.get(dataset_metadata_url)
        res.raise_for_status()
        dataset_metadata = res.json()

        # get the latest version of the dataset
        latest_version = dataset_metadata["_links"]["stash:version"]["href"]
        return f"https://datadryad.org{latest_version}/files"

    def _get_attr_link(self, record, base_url):
        return base_url + record["_links"]["stash:download"]["href"]


class DataOneDataset(DatasetDownloader):
    """Downloader for DataOne repositories."""

    REGEXP_ID = r"view/doi:(?P<record_id>.*)"

    # the base entry point of the REST API
    API_URL = "https://cn.dataone.org/cn/v2/object/"

    @property
    def files(self):
        if hasattr(self, "_files"):
            return self._files

        doi_safe = quote(f"doi:{self._params['record_id']}", safe="")

        res = requests.get(self.API_URL + doi_safe)
        res.raise_for_status()
        meta_tree = ET.fromstring(res.content)

        x = []
        for data_elem in meta_tree.find("dataset"):
            if data_elem.tag in ["otherEntity", "dataTable"]:
                x.append(
                    {
                        "link": data_elem.find(
                            "./physical/distribution/online/url[@function='download']"
                        ).text,
                        "name": data_elem.find("entityName").text,
                        "size": data_elem.find("./physical/size").text,
                        "hash": None,
                        "hash_type": None,
                    }
                )

        self._files = x
        return self._files


class PangaeaDataset(DatasetDownloader):
    """Downloader for PangaeaDataset repository."""

    REGEXP_ID = r"doi\.pangaea\.de/(?P<record_id>.*)"

    # the base entry point of the REST API
    API_URL = "https://doi.pangaea.de/"

    @property
    def files(self):
        # get the difference between collection and file
        r = requests.get(
            f"{self.API_URL}{self._params['record_id']}?format=metadata_jsonld"
        )
        r.raise_for_status()
        dists = r.json()["distribution"]

        if isinstance(dists, dict):
            dists = [dists]

        files = []
        for d in dists:
            if d["encodingFormat"] in ["text/tab-separated-values", "application/zip"]:
                r_filename = requests.head(d["contentUrl"])
                content_d = r_filename.headers["content-disposition"]

                files.append(
                    {
                        "link": d["contentUrl"],
                        "name": re.findall("filename=(.+)", content_d)[0],
                        "size": None,
                        "hash": None,
                        "hash_type": None,
                    }
                )

        return files


class DSpaceDataset(DatasetDownloader):
    """Downloader for DSpaceDataset repositories."""

    REGEXP_ID = r"handle/(?P<record_id>\d+\/\d+)"

    # paths to file attributes
    ATTR_KIND_JSONPATH = "attributes.kind"

    ATTR_FILE_LINK_JSONPATH = "link"

    ATTR_NAME_JSONPATH = "name"
    ATTR_SIZE_JSONPATH = "sizeBytes"
    ATTR_HASH_JSONPATH = "checkSum.checkSumAlgorithm"
    ATTR_HASH_TYPE_VALUE = "checkSum.value"

    def _get_attr_link(self, record, base_url):
        return base_url + record["retrieveLink"]

    @property
    def API_URL_META(self):
        uri = urlparse(_get_url(self.resource))
        base_url = uri.scheme + "://" + uri.netloc

        handle_id_url = f"{base_url}/rest/handle/{self._params['record_id']}"
        res = requests.get(handle_id_url)
        res.raise_for_status()

        return base_url + res.json()["link"] + "/bitstreams"


class FigShareDataset(DatasetDownloader):
    """Downloader for FigShare repository."""

    REGEXP_ID = r"articles\/.*?\/.*?\/(?P<record_id>\d+)(?:\/(?P<version>\d+)|)"

    # the base entry point of the REST API
    API_URL = "https://api.figshare.com/v2"

    # the files and metadata about the dataset
    META_FILES_JSONPATH = "files[*]"

    # paths to file attributes
    ATTR_FILE_LINK_JSONPATH = "download_url"
    ATTR_NAME_JSONPATH = "name"
    ATTR_SIZE_JSONPATH = "size"
    ATTR_HASH_JSONPATH = "computed_md5"
    ATTR_HASH_TYPE_VALUE = "md5"

    @property
    def API_URL_META(self):
        s = "{api_url}/articles/{record_id}"

        if self._params.get("version", None):
            s += "/versions/{version}"

        return s


class DjehutyDataset(FigShareDataset):
    """Downloader for Djehuty repository."""

    REGEXP_ID = r"articles\/.*?\/(?P<record_id>\d+)(?:\/(?P<version>\d+)|)"

    # the base entry point of the REST API
    API_URL = "https://data.4tu.nl/v2"


class GitHubDataset(DatasetDownloader):
    """Downloader for GitHub repository."""

    API_URL = "https://github.com/"
    REGEXP_ID = r"github\.com\/(?P<record_id>[a-zA-Z0-9]+\/[a-zA-Z0-9]+)[\/]*.*"

    def _get(self, output_folder: Union[Path, str], *args, **kwargs):
        res = requests.get(
            f"{self.API_URL}{self._params['record_id']}/archive/refs/heads/master.zip"
        )
        z = zipfile.ZipFile(io.BytesIO(res.content))
        z.extractall(output_folder)

    @property
    def files(self):
        # at the moment, .files is not available for GitHub
        raise NotImplementedError("'files' is not available for GitHub")


class HuggingFaceDataset(DatasetDownloader):
    """Downloader for Huggingface repository."""

    REGEXP_ID = r"huggingface.co/datasets/(?P<record_id>.*)"

    def _get(
        self,
        output_folder: Union[Path, str],
    ):
        try:
            from datasets import load_dataset
        except ImportError as err:
            raise ImportError(
                "Install 'datasets' to use HuggingFace Datasets"
                " or use 'pip install datahugger[all]'"
            ) from err

        params = self.params if self.params else {}
        load_dataset(self._params["record_id"], cache_dir=output_folder, **params)

    @property
    def files(self):
        # at the moment, .files is not available for HuggingFace
        raise NotImplementedError("'files' is not available for HuggingFace")


class HuggingFaceModel(DatasetDownloader):
    """Downloader for Hugging Face model repositories.

    Takes the address of a model page, e.g.
    https://huggingface.co/google/vit-base-patch16-224, optionally with a
    branch, tag or commit: .../tree/<revision>. The revision is resolved to
    its commit first, so all files come from the same snapshot of the model.

    Params (``-p key=value`` on the command line):

    - ``revision``: branch, tag or commit; overrides the one in the URL.
    - ``include``: comma separated glob patterns of the files to download,
      e.g. ``*.safetensors,*.json``.
    - ``exclude``: comma separated glob patterns of the files to skip.

    Only public models are supported: gated and private models need an
    access token, which this downloader does not send.
    """

    API_URL = "https://huggingface.co/api/models/"
    BASE_URL = "https://huggingface.co/"

    # Top-level paths of huggingface.co that are not model namespaces.
    RESERVED = (
        "api",
        "blog",
        "collections",
        "datasets",
        "docs",
        "models",
        "organizations",
        "papers",
        "settings",
        "spaces",
    )

    REGEXP_ID = (
        r"huggingface\.co/"
        r"(?!(?:" + "|".join(RESERVED) + r")(?:/|$|\?))"
        r"(?P<record_id>[\w.-]+/[\w.-]+)"
        r"(?:/(?:tree|blob|resolve)/(?P<version>[^/?#]+))?"
    )

    def _option(self, name):
        """Return a user param, or None if it is not set or empty."""
        value = (self.params or {}).get(name)
        return value if value not in (None, "") else None

    def _patterns(self, name):
        value = self._option(name)
        if value is None:
            return []
        if isinstance(value, str):
            value = value.split(",")
        return [pattern.strip() for pattern in value if pattern.strip()]

    def _selected(self, path):
        """Tell if a file passes the include and exclude patterns."""
        include = self._patterns("include")
        exclude = self._patterns("exclude")
        if include and not any(fnmatch(path, p) for p in include):
            return False
        return not any(fnmatch(path, p) for p in exclude)

    def _get_json(self, url):
        res = requests.get(url)
        if res.status_code in (401, 403):
            raise ValueError(
                f"No access to '{self._params['record_id']}' on Hugging Face. "
                "It may be private or gated, or not exist; "
                "only public models are supported."
            )
        if res.status_code == 404:
            raise ValueError(
                f"Model or revision not found on Hugging Face: "
                f"'{self._params['record_id']}'"
            )
        res.raise_for_status()
        return res

    @property
    def commit(self):
        """The commit the requested revision points to."""
        if not hasattr(self, "_commit"):
            revision = self._option("revision") or self._params.get("version") or "main"
            url = (
                f"{self.API_URL}{self._params['record_id']}"
                f"/revision/{quote(revision, safe='')}"
            )
            self._commit = self._get_json(url).json()["sha"]
        return self._commit

    @property
    def files(self):
        if hasattr(self, "_files"):
            return self._files

        repo_id = self._params["record_id"]
        url = f"{self.API_URL}{repo_id}/tree/{self.commit}?recursive=true"

        entries = []
        while url:
            res = self._get_json(url)
            entries.extend(res.json())
            # the listing of large repositories comes in pages
            url = res.links.get("next", {}).get("url")

        files = []
        for entry in entries:
            if entry.get("type") != "file" or not self._selected(entry["path"]):
                continue
            # Files stored with Git LFS (the weights) have a sha256 checksum.
            # The oid of other files is a git blob hash, not a file checksum.
            lfs = entry.get("lfs") or {}
            files.append(
                {
                    "link": (
                        f"{self.BASE_URL}{repo_id}/resolve/{self.commit}/"
                        f"{quote(entry['path'])}"
                    ),
                    "name": entry["path"],
                    "size": entry.get("size"),
                    "hash": lfs.get("oid"),
                    "hash_type": "sha256" if lfs.get("oid") else None,
                }
            )

        self._files = files
        return self._files


class MendeleyDataset(DatasetDownloader):
    """Downloader for Mendeley repository."""

    REGEXP_ID = r"data\.mendeley\.com\/datasets\/(?P<record_id>[0-9a-z]+)(?:\/(?P<version>\d+)|)"  # noqa

    # the base entry point of the REST API
    API_URL = "https://data.mendeley.com/public-api/"

    # version url
    API_URL_VERSION = "{api_url}datasets/{record_id}/versions"

    # the files and metadata about the dataset
    API_URL_META = (
        "{api_url}datasets/{record_id}/files?folder_id=root&version={version}"
    )

    # paths to file attributes
    ATTR_FILE_LINK_JSONPATH = "content_details.download_url"
    ATTR_NAME_JSONPATH = "filename"
    ATTR_SIZE_JSONPATH = "size"
    ATTR_HASH_JSONPATH = "content_details.sha256_hash"
    ATTR_HASH_TYPE_VALUE = "sha256"


class OSFDataset(DatasetDownloader):
    """Downloader for OSF repository."""

    REGEXP_ID = r"osf\.io\/(?P<record_id>[^\/]*)\/{0,1}"

    # the base entry point of the REST API
    API_URL = "https://api.osf.io/v2/nodes/"

    # the files and metadata about the dataset
    API_URL_META = "{api_url}{record_id}/files/"
    META_FILES_JSONPATH = "data[*]"

    PAGINATION_JSONPATH = "links.next"

    # paths to file attributes
    ATTR_KIND_JSONPATH = "attributes.kind"

    ATTR_FILE_LINK_JSONPATH = "links.download"
    ATTR_FOLDER_LINK_JSONPATH = "relationships.files.links.related.href"

    ATTR_NAME_JSONPATH = "attributes.name"
    ATTR_SIZE_JSONPATH = "attributes.size"
    ATTR_HASH_JSONPATH = "attributes.extra.hashes.sha256"
    ATTR_HASH_TYPE_VALUE = "sha256"

    def _get_node_providers(self):
        """Get the providers of a node."""
        record_id = self._params["record_id"]
        res = requests.get(f"{self.API_URL}/{record_id}/files/")
        return set([prov["attributes"]["provider"] for prov in res.json()["data"]])

    def _get_files_recursive(self, url, folder_name=None, base_url=None):
        files = []
        # In case of the top-level folder, we need to get first the providers
        if folder_name is None:
            for provider in self._get_node_providers():
                # and then the files of each provider
                files.extend(
                    super()._get_files_recursive(f"{url}{provider}/", None, base_url)
                )
        else:
            files = super()._get_files_recursive(url, folder_name, base_url)
        return files


class ZenodoDataset(DatasetDownloader):
    """Downloader for Zenodo repository.

    For Zenodo records, new versions have new identifiers.
    """

    REGEXP_ID = r"zenodo\.org\/record(s*)\/(?P<record_id>\d+).*"

    # the base entry point of the REST API
    API_URL = "https://zenodo.org/api/"

    # the files and metadata about the dataset
    API_URL_META = "{api_url}records/{record_id}"
    META_FILES_JSONPATH = "files[*]"

    # paths to file attributes
    ATTR_NAME_JSONPATH = "key"
    ATTR_FILE_LINK_JSONPATH = "links.self"
    ATTR_SIZE_JSONPATH = "size"
    ATTR_HASH_JSONPATH = "checksum"

    def _get_attr_hash(self, record):
        return self._get_attr_attr(record, self.ATTR_HASH_JSONPATH).split(":")[1]

    def _get_attr_hash_type(self, record):
        return self._get_attr_attr(record, self.ATTR_HASH_JSONPATH).split(":")[0]


class DataEuropaDataset(DatasetDownloader):
    """Downloader for European data repository."""

    REGEXP_ID = r"data\.europa\.eu\/data\/datasets\/(?P<record_id>.+)"

    # the base entry point of the REST API
    API_URL = "https://data.europa.eu/api/hub/repo/"

    API_URL_META = "{api_url}datasets/{record_id}"
    META_FILES_JSONPATH = '$.@graph[?(@.@type == "dcat:Distribution")]'

    # paths to file attributes
    ATTR_FILE_LINK_JSONPATH = "'dcat:accessURL'.@id"
    ATTR_NAME_JSONPATH = "'dct:title'"
    ATTR_SIZE_JSONPATH = "'dcat:byteSize'.@value"


class SeaNoeDataset(DatasetDownloader):
    """Downloader for SeaNoe publication."""

    REGEXP_ID = r"https://www.seanoe\.org/data/[0-9]+/(?P<record_id>.*)/"

    # the base entry point of the REST API
    API_URL = "https://www.seanoe.org/api/"

    # the files and metadata about the dataset
    API_URL_META = "{api_url}find-by-id/{record_id}"
    META_FILES_JSONPATH = "files[*]"

    # paths to file attributes
    ATTR_NAME_JSONPATH = "fileName"
    ATTR_FILE_LINK_JSONPATH = "fileUrl"
    ATTR_SIZE_JSONPATH = "size"
    ATTR_HASH_JSONPATH = "checksum"
    ATTR_HASH_TYPE_VALUE = "sha256"


class B2shareDataset(DatasetDownloader):
    """Downloader for B2Share repository."""

    REGEXP_ID = r"b2share\.eudat\.eu\/records\/(?P<record_id>[0-9a-z]+)"

    # the base entry point of the REST API
    API_URL = "https://b2share.eudat.eu/api/"

    # the files and metadata about the dataset
    API_URL_META = "{api_url}records/{record_id}"
    META_FILES_JSONPATH = "files[*]"

    # paths to file attributes
    ATTR_NAME_JSONPATH = "key"
    ATTR_FILE_LINK_JSONPATH = "ePIC_PID"
    ATTR_SIZE_JSONPATH = "size"
    ATTR_HASH_JSONPATH = "checksum"
    ATTR_HASH_TYPE_VALUE = "md5"
