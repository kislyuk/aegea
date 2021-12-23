import os

class GitHub:
    _session = None

    @classmethod
    def session(cls):
        if cls._session is None:
            from ..packages import github3
            try:
                cls._session = github3.login(token=os.environ["GH_AUTH"])
            except Exception:
                msg = "GitHub login failed. Please get a token at https://github.com/settings/tokens and set the GH_AUTH environment variable to its value." # noqa
                raise Exception(msg)
        return cls._session

def parse_repo_name(repo_url):
    if repo_url.endswith(".git"):
        repo_url = repo_url[:-len(".git")]
    repo_url = repo_url.split(":")[-1]
    gh_owner_name, gh_repo_name = repo_url.split("/")[-2:]
    return gh_owner_name, gh_repo_name

def get_repo(url):
    gh_owner_name, gh_repo_name = parse_repo_name(url)
    return GitHub.session().repository(gh_owner_name, gh_repo_name)

def private_submodules(url):
    repo = get_repo(url)
    head_sha = repo.ref(os.path.join("heads", repo.default_branch)).object.sha
    for tree in repo.tree(head_sha).recurse().tree:
        if tree.type == "commit":
            submodule_url = repo.contents(tree.path).submodule_git_url
            for submodule in private_submodules(submodule_url):
                yield submodule
            if get_repo(submodule_url).private:
                yield submodule_url
