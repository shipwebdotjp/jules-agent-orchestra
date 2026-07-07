# Release

`jules-agent` を更新して PyPI に再公開する手順。

## 1. バージョンを上げる

`pyproject.toml` と `src/jules_agent/__init__.py` の `__version__` を同じ値にする。

例:

```toml
# pyproject.toml
version = "0.1.3"
```

```python
# src/jules_agent/__init__.py
__version__ = "0.1.3"
```

## 2. 変更内容を確認する

- CLI コマンドを変えたら `README.md` も更新する
- config の設定値を変えたら `README.md` も更新する
- 必要なら `README_ja.md` も合わせて更新する

## 3. 配布物を作り直す

古い生成物が残っていると見づらいので、先に消してからビルドする。

```bash
rm -rf dist build src/jules_agent.egg-info
uv run --with build python -m build
```

## 4. メタデータを検査する

```bash
uv run --with twine twine check dist/*
```

## 5. TestPyPI に上げる

```bash
uv run --with twine twine upload --repository testpypi dist/*
```

## 6. TestPyPI からインストール確認する

TestPyPI 単独だと依存パッケージが見つからないことがあるので、`pypi.org` も併用する。

```bash
python -m pip install \
  --index-url https://test.pypi.org/simple/ \
  --extra-index-url https://pypi.org/simple \
  jules-agent==0.1.3
```

確認するコマンド:

```bash
jules-agent --help
```

必要なら、主要コマンドを 1 つだけ追加で試す。

## 7. 本番 PyPI に上げる

TestPyPI で問題がなければ、本番に同じ生成物を上げる。

```bash
uv run --with twine twine upload --repository pypi dist/*
```

## 8. 本番 PyPI からインストール確認する

```bash
python -m pip install jules-agent==0.1.3
```

## 注意点

- PyPI は同じバージョンを再アップロードできない
- `README.md` のインストール例は公開先に合わせる
- 依存関係が TestPyPI に存在しない場合は、TestPyPI 単独の `pip install -i https://test.pypi.org/simple/ ...` は失敗する
