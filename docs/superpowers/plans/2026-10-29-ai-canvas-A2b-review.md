# A2b 独立验收与待修复项

## 当前状态

OpenCode 已写入 A2b API、生命周期、Unicode 校验及相关测试，但会话 `ses_e7ab8a50effe2uqgsrgrjEEMH1` 在最终交付前收到 `provider.free-limit` / HTTP 429。尚未将 A2b 标记完成，不切换未获授权的模型。

已完成的独立验证：

- 六份设计测试文件 196 passed；ruff check 通过。
- A2b 变更符合任务白名单，未改 repository/rendering/demo 和其他旧业务文件。
- 在临时数据目录启动真实本机服务，health、上传创建、固定版本 SVG、响应保护头、改价、同 key 重放、过期 revision 409、撤销/重做、历史预览不变、非法 XML 文案 422、跨站拒绝共 10 项验证通过。另已真实停止并重启服务，revision 4、价格 88.80 与素材 SVG 仍正确。测试服务均已停止，用户原工作台未停止。
- 当前已落盘代码的完整回归：830 passed in 132.90s；ruff format 检查通过，17 files already formatted。此结果不覆盖独立探测发现但尚未加入测试集的文件别名缺陷，不能据此标记 A2b 已完成。

## 阻断项：同一数据库的文件别名绕过独占所有权

最小复现：在临时目录创建 `DesignManager(real.sqlite3, assets-a)` 并调用 repository()，再创建 `alias.sqlite3` 符号链接指向 real.sqlite3，最后 `DesignManager(alias.sqlite3, assets-b).repository()`。

实际：两个 manager 都取得同一个物理数据库的访问权。根因是 service.py 按配置文件名生成旁路锁，而数据库别名使用另一个锁名；数据库内容实际共享。独立探测也覆盖硬链接别名，结果同样允许双持有。

实现方继续任务时只允许修改 `app/designs/service.py`、`tests/test_design_lifecycle.py` 和 foundation 文档，先加真实失败回归，再修复。选择将受支持的路径规范化后共用锁，或明确拒绝不安全的数据库别名；无论选择哪种，符号链接和硬链接都不能让第二个 manager 同时访问同一数据库。不要关闭原有锁、捕获冲突后继续运行，也不要扩大到旧 worker_lock 重构。

必须保持：旧接口不初始化设计存储、首次设计请求才获得锁、初始化失败释放已得锁、并发初始化单实例、释放后可重新打开、配置不能被请求修改、错误不泄漏绝对路径。

修复后协调方重跑六份设计测试、真实别名探测、完整回归及服务重启后的精确版本读取。通过后才将 A2b 和整体 A2 标记完成，然后进入画布和渲染验证。
