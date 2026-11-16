# 画布审核与导出

打开设计并保存属性草稿后，点击画布顶部“审核与导出”。此时画布为只读，面板显示当前保存版本。核对文案、价格及商品图片，勾选确认项，再点击“确认当前版本”。随后可下载 SVG 或 PNG。

SVG 为服务端渲染的自包含矢量文件，文件名包含设计 ID 和版本。PNG 使用现有可选 CairoSVG 渲染器。需要 PNG 时可在启动服务的环境显式安装渲染依赖，系统还需有 Cairo 动态库。

```bash
uv run --locked --extra mcp --with 'CairoSVG>=2.7,<3' python -m app.run_local --port 8000
```

PNG 还需要 Fontconfig 的 `fc-match` 工具，以及文档使用的 Noto Sans CJK SC 字体和字重。Linux 可安装 `fonts-noto-cjk`、`fontconfig` 和 `libcairo2`。服务在渲染前检查字体匹配，字体被替换时返回明确错误。画布尺寸沿用当前文档，PNG 使用后端的完整性及尺寸检查。[Noto CJK 官方字体](https://github.com/notofonts/noto-cjk)

Apple Silicon macOS 若已通过 Homebrew 安装 Cairo，但启动链清除了动态库环境变量，可在 Python 进程内设置已安装库的路径后启动服务。

```bash
uv run --locked --extra mcp --with 'CairoSVG>=2.7,<3' python -c \
  'import os; os.environ["DYLD_FALLBACK_LIBRARY_PATH"]="/opt/homebrew/lib"; from app.run_local import main; main()' --port 8000
```

本机若使用独立字体目录，可通过 `FONTCONFIG_FILE` 指定进程专用配置。以下配置中的路径需替换为已准备的字体目录及可写缓存目录，字体文件仅供本次服务使用。[Fontconfig 配置](https://fontconfig.pages.freedesktop.org/fontconfig/fontconfig-user.html)

```xml
<?xml version="1.0"?>
<!DOCTYPE fontconfig SYSTEM "fonts.dtd">
<fontconfig>
  <dir>/absolute/path/to/noto-fonts</dir>
  <cachedir>/absolute/path/to/font-cache</cachedir>
</fontconfig>
```

macOS 渲染时还会通过 CoreText 将匹配到的字体注册到当前进程，使 Cairo 的原生字体后端可以使用这些字体。[Apple 进程字体注册](https://developer.apple.com/documentation/coretext/ctfontmanagerregisterfontsforurl(_:_:_:))

依赖不可用时，面板提示原因，SVG 下载继续可用。

服务配置 `POSTER_DESIGN_REVIEWERS_JSON` 时，在面板输入相应审核凭据。确认需要 `approve` 权限，下载需要 `export` 权限。凭据仅保留在此面板的内存中，关闭、切换设计或刷新后重新输入。

确认请求绑定精确版本及服务端计算的设计和素材摘要。同一面板重试复用原确认 ID，保存响应丢失时可重试。后续编辑会让旧版本的下载请求失败，重新载入设计后核对新版本。正式发布、OCR 和视觉质量评价继续使用各自的既有流程。

自动浏览器验证使用程序素材，覆盖逐字节 SVG 下载、确认响应丢失、版本冲突、刷新恢复、审核凭据和手机布局。以下命令还要求 PNG 渲染和真实下载成功。

```bash
uv run --locked --extra mcp --with 'playwright>=1.55,<2' --with 'CairoSVG>=2.7,<3' \
  python tests/browser_canvas_export.py --require-png
```
