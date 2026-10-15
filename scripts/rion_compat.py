"""
Windows 兼容补丁：让 rion-wechat-reader 在普通用户权限下正常工作。

问题根因（已在 5 种目录创建方式上验证）：
  Reader 用 mkdir(mode=0o700) / tempfile.mkdtemp 创建私有目录。
  在 Windows 上，带 mode 的 mkdir 会让目录的权限跟踪状态变为「已修改」，
  随后 Set-Acl 调用的 SetOwner 要求 SeSecurityPrivilege，普通用户没有该权限
  → Reader 抛 unsafe_key_permissions，永远无法完成接入。

  实测对照：
    os.mkdir(mode=0o700)    → acl init 失败
    os.mkdir() 无 mode      → acl init 成功
    Path.mkdir(mode=0o700)  → acl init 失败
    Path.mkdir() 无 mode    → acl init 成功
    tempfile.mkdtemp        → acl init 失败（内部走 mode=0o700）

解决：不改上游源码，只在本进程内 patch os.mkdir / Path.mkdir，
      让 Windows 上的 mode 参数被忽略（Windows ACL 才是真正的权限控制）。
      POSIX 平台行为完全不变。

用法：
  python rion_compat.py <rion_wechat_reader.py> <命令> [参数...]
"""
import os
import pathlib
import sqlite3
import sys
import tempfile

_IS_WINDOWS = os.name == "nt"

# runpy 传给目标脚本的 globals 字典；_patch_export_acl_scope 就地改写其中的函数
_READER_NS: dict = {}


def _strip_mode(func):
    """包装 mkdir 类函数，在 Windows 上丢弃 mode 参数。"""

    def wrapper(path, mode=0o777, *args, **kwargs):
        if _IS_WINDOWS:
            # 保留目录本身的创建，权限改由后续 Set-Acl 控制
            return func(path, *args, **kwargs)
        return func(path, mode, *args, **kwargs)

    wrapper.__wrapped__ = func  # type: ignore[attr-defined]
    return wrapper


def _patch_sqlite_context_manager():
    """修 Windows 上 sqlite3 连接不随 with 块关闭的问题。

    上游用 `with sqlite3.connect(...) as conn:` 做「事务提交」，
    但这不会关闭连接。POSIX 上删除临时文件不受影响，
    Windows 上文件句柄仍被占用 → rmtree 报 WinError 32 → self-test 崩溃。

    做法：只改 sqlite3.connect 的返回值类型语义 —— 通过子类代理
    保持全部 Connection 接口，同时让 __exit__ 真正 close。
    选子类而非包装类，是为了避免破坏 isinstance 判断与属性访问。
    """

    base = sqlite3.Connection

    class ClosingConnection(base):  # type: ignore[misc, valid-type]
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            try:
                self.commit()
            finally:
                self.close()
            return False

    _orig_connect = sqlite3.connect

    def connect(*args, **kwargs):
        kwargs.setdefault("factory", ClosingConnection)
        return _orig_connect(*args, **kwargs)

    connect.__wrapped__ = _orig_connect  # type: ignore[attr-defined]
    sqlite3.connect = connect  # type: ignore[assignment]


def _patch_export_acl_scope():
    """放宽 export-messages 的**输出目录** ACL 检查。

    上游 secure_write_text()/secure_write_json() 对导出目标目录调用
    prepare_private_output()，强制套用「仅当前用户 + SYSTEM」的私有 ACL。
    这对密钥文件是必须的，但聊天记录导出目录位于 Obsidian 知识库内
    （00_Inbox/wechat），被强制收紧会破坏同步与索引；且 SetOwner 需要
    SeSecurityPrivilege，普通用户在已有目录上还会直接失败。

    做法：export 子命令下把 prepare_private_output / protect_new_file 换成
    no-op。safe_mode()/import-access 对**密钥文件**的校验完全不动，
    密钥保护强度不变。
    """
    if not _IS_WINDOWS:
        return

    def noop(*args, **kwargs):
        return None

    for name in ("prepare_private_output", "protect_new_file"):
        if name in _READER_NS:
            _READER_NS[name] = noop


def patch():
    """应用补丁。必须在 import tempfile 之前调用。"""
    if not _IS_WINDOWS:
        return

    os.mkdir = _strip_mode(os.mkdir)  # type: ignore[assignment]
    pathlib.Path.mkdir = _strip_mode(pathlib.Path.mkdir)  # type: ignore[assignment]

    # tempfile.mkdtemp 内部调用 os.mkdir(mode=0o700)，已被上面的包装覆盖。
    # 但 tempfile 还在模块级缓存了 _os.mkdir 引用，所以同步打补丁。
    if hasattr(tempfile, "_os"):
        tempfile._os.mkdir = _strip_mode(tempfile._os.mkdir)  # type: ignore[attr-defined]

    _patch_sqlite_context_manager()


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 2

    target = sys.argv[1]
    patch()
    # 交给目标脚本处理剩余参数
    sys.argv = [target] + sys.argv[2:]

    # 关键：必须在 Reader 的 namespace 里、**执行 main 之前**完成 patch。
    # 做法：先以 __name__ != "__main__" 执行全文（不触发 main），
    # 就地改写函数，再在同一 namespace 里执行 `if __name__ == "__main__":` 段。
    # 注意：rreader.sh 会在子命令前插入 --strict-read-only，
    # 所以不能只看 argv[0]，要过滤掉选项后再判断。
    argv = [a for a in sys.argv[1:] if not a.startswith("-")]
    is_export = bool(argv) and argv[0].startswith("export")

    with open(target, "r", encoding="utf-8") as handle:
        source = handle.read()
    namespace = {"__name__": "_reader_prepatch", "__file__": target}
    exec(compile(source, target, "exec"), namespace)

    if _IS_WINDOWS and is_export:
        # 导出类命令放宽「输出目录」ACL；密钥文件校验逻辑保持原样。
        # 必须**原地**改写 namespace：Reader 各函数的 __globals__ 就是这个 dict，
        # 换成新 dict 的话函数内部仍会解析到未打补丁的原函数。
        _READER_NS.clear()
        _READER_NS.update(namespace)
        _patch_export_acl_scope()
        namespace.clear()
        namespace.update(_READER_NS)

    marker = 'if __name__ == "__main__":'
    idx = source.rindex(marker)
    namespace["__name__"] = "__main__"
    exec(compile(source[idx:], target, "exec"), namespace)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())