"""为作品集从零编写的通用 Mock 教学演示，不是生产系统源码，不生成真实视频。"""
import argparse
import json
import sqlite3
import tempfile
from pathlib import Path


class SubmissionUnknown(Exception):
    """远端可能已接收请求，本地没有拿到可核对的任务号。"""


class MockProvider:
    """只返回内存中的模拟结果；不联网、不调用模型、不写媒体文件。"""

    def __init__(self):
        self.calls = {"submit": 0, "query": 0}
        self.unknown_next = False

    def submit(self):
        self.calls["submit"] += 1
        if self.unknown_next:
            self.unknown_next = False
            raise SubmissionUnknown("模拟：请求已发出，但响应丢失")
        return f"mock-{self.calls['submit']}"

    def query(self, remote_id):
        self.calls["query"] += 1
        return f"mock-result:{remote_id}"


class Workflow:
    """单进程串行教学流程。调用方必须明确提供数据库路径。"""

    def __init__(self, db_path, provider):
        self.provider = provider
        self.db = sqlite3.connect(db_path)
        self.db.row_factory = sqlite3.Row
        with self.db:
            self.db.execute("""CREATE TABLE IF NOT EXISTS tasks (
                id INTEGER PRIMARY KEY, business_key TEXT UNIQUE NOT NULL,
                version INTEGER NOT NULL DEFAULT 1, state TEXT NOT NULL,
                remote_id TEXT, result TEXT, reviewed_version INTEGER)""")

    def close(self):
        self.db.close()

    def get(self, key):
        row = self.db.execute(
            "SELECT * FROM tasks WHERE business_key = ?", (key,)).fetchone()
        if row is None:
            raise KeyError(key)
        return dict(row)

    def request(self, key):
        if not key.strip():
            raise ValueError("业务键不能为空")
        with self.db:
            self.db.execute("INSERT OR IGNORE INTO tasks (business_key, state) "
                            "VALUES (?, 'queued')", (key,))
        return self.get(key)

    def advance(self, key):
        task = self.get(key)
        if task["state"] in {"generated", "approved", "needs_attention"}:
            return task  # 已有结果直接复用；结果未知不自动重提。
        if task["remote_id"]:
            result = self.provider.query(task["remote_id"])
            with self.db:
                self.db.execute("UPDATE tasks SET state='generated', result=? "
                                "WHERE business_key=?", (result, key))
        elif task["state"] == "queued":
            # 先持久化提交意图：即使响应前中断，恢复时也不会盲目重提。
            with self.db:
                self.db.execute("UPDATE tasks SET state='submitting' "
                                "WHERE business_key=?", (key,))
            try:
                remote_id = self.provider.submit()
            except SubmissionUnknown:
                with self.db:
                    self.db.execute("UPDATE tasks SET state='needs_attention' "
                                    "WHERE business_key=?", (key,))
            else:
                with self.db:
                    self.db.execute("UPDATE tasks SET state='submitted', remote_id=? "
                                    "WHERE business_key=?", (remote_id, key))
        else:  # 留在 submitting 且无任务号：上次提交结果不明，交给人工核对。
            with self.db:
                self.db.execute("UPDATE tasks SET state='needs_attention' "
                                "WHERE business_key=?", (key,))
        return self.get(key)

    def approve(self, key, material_version):
        # 审核的是某一版本的素材；过期页面不能批准新版本。
        with self.db:
            changed = self.db.execute(
                "UPDATE tasks SET state='approved', reviewed_version=? "
                "WHERE business_key=? AND version=? AND state='generated'",
                (material_version, key, material_version)).rowcount
            if changed != 1:
                raise ValueError("素材版本不匹配，或当前没有可审核结果")
        return self.get(key)

    def redo(self, key):
        with self.db:
            changed = self.db.execute(
                "UPDATE tasks SET version=version+1, state='queued', "
                "remote_id=NULL, result=NULL, reviewed_version=NULL "
                "WHERE business_key=? AND state IN ('generated', 'approved')",
                (key,)).rowcount
            if changed != 1:
                raise ValueError("仅已生成的素材可重做；不明确的提交须先人工核对")
        return self.get(key)


def show(label, task):
    print(label, json.dumps(task, ensure_ascii=False, sort_keys=True))


def demo(db_path):
    provider = MockProvider()
    flow = Workflow(db_path, provider)
    try:
        first = flow.request("sample")
        show("重复业务键复用同一任务", flow.request("sample"))
        assert first["id"] == flow.get("sample")["id"]
        show("首次提交", flow.advance("sample"))
        flow.close()
        flow = Workflow(db_path, provider)
        show("重开数据库后查询原远端任务", flow.advance("sample"))
        flow.close()
        flow = Workflow(db_path, provider)
        show("重开数据库复用已完成结果", flow.advance("sample"))
        show("人工审核版本 1", flow.approve("sample", 1))
        show("重做升级到版本 2", flow.redo("sample"))
        flow.advance("sample")
        flow.advance("sample")
        try:
            flow.approve("sample", 1)
        except ValueError as error:
            print("旧审核被拒绝:", error)
        show("人工审核版本 2", flow.approve("sample", 2))
        flow.request("uncertain")
        provider.unknown_next = True
        show("远端结果未知", flow.advance("uncertain"))
        show("再次推进仍等待人工核对", flow.advance("uncertain"))
        print("MockProvider calls:", provider.calls)
    finally:
        flow.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    choice = parser.add_mutually_exclusive_group(required=True)
    choice.add_argument("--demo", action="store_true", help="只在临时目录演示")
    choice.add_argument("--db", type=Path, help="仅初始化并查看显式指定的数据库")
    args = parser.parse_args()
    if args.demo:
        with tempfile.TemporaryDirectory(prefix="portfolio-mock-") as folder:
            demo(Path(folder) / "workflow.sqlite3")
    else:
        flow = Workflow(args.db, MockProvider())
        try:
            rows = [dict(row) for row in flow.db.execute("SELECT * FROM tasks")]
            print(json.dumps(rows, ensure_ascii=False, indent=2))
        finally:
            flow.close()


if __name__ == "__main__":
    main()
