import {
    Aperture,
    Layers3,
    ImagePlus,
    ListChecks,
    History,
    HardDrive,
    ArrowUpRight,
} from 'lucide-react';
import { CreateWorkspace } from './components/CreateWorkspace';
import { BatchWorkspace } from './components/BatchWorkspace';
import { ReviewWorkspace } from './components/ReviewWorkspace';
import { HistoryWorkspace } from './components/HistoryWorkspace';

const navigation = [
    { id: 'create', label: '创作海报', Icon: ImagePlus },
    { id: 'batch', label: '批量处理', Icon: Layers3 },
    { id: 'review', label: '审核中心', Icon: ListChecks },
    { id: 'history', label: '执行记录', Icon: History },
];

export function App() {
    return (
        <>
            <a href="#workspaceMain" className="skip-link">
                跳到工作区
            </a>
            <aside className="app-sidebar">
                <header className="app-header">
                    <a className="brand" href="#create" aria-label="Poster Agent 创作首页">
                        <span className="brand-mark">
                            <Aperture size={25} strokeWidth={1.7} />
                        </span>
                        <div>
                            <h1>
                                Poster Agent<span className="brand-dot">.</span>
                            </h1>
                            <p className="sub">商品海报工作台</p>
                        </div>
                    </a>
                </header>
                <p className="sidebar-caption">工作空间</p>
                <nav className="workspace-nav" aria-label="工作区">
                    <div role="tablist" aria-label="工作区导航" aria-orientation="vertical">
                        {navigation.map(({ id, label, Icon }, index) => (
                            <button
                                key={id}
                                id={`nav-${id}`}
                                role="tab"
                                aria-selected={index === 0}
                                aria-controls={`view-${id}`}
                                tabIndex={index === 0 ? 0 : -1}
                                data-workspace={id}
                            >
                                <Icon size={19} strokeWidth={1.7} />
                                <span>{label}</span>
                            </button>
                        ))}
                    </div>
                </nav>
                <div className="sidebar-bottom">
                    <div className="local-indicator">
                        <HardDrive size={16} />
                        <div>
                            <strong>本地工作区</strong>
                            <p>素材与记录保存在本机</p>
                        </div>
                        <span className="status-dot" />
                    </div>
                    <a href="/docs" target="_blank" rel="noreferrer" className="docs-link">
                        接口文档
                        <ArrowUpRight size={14} />
                    </a>
                </div>
            </aside>
            <div className="workspace-body">
                <div className="topbar">
                    <span>
                        工作台 <span className="mx-3 text-slate-300">/</span> 电商营销创作
                    </span>
                    <span className="topbar-status">
                        <span className="status-dot" /> 本地运行
                    </span>
                </div>
                <main className="shell" id="workspaceMain" tabIndex={-1}>
                    <CreateWorkspace />
                    <BatchWorkspace />
                    <ReviewWorkspace />
                    <HistoryWorkspace />
                </main>
                <footer className="app-footer">
                    文件与记录保存在本机。生成与自动检查完成后，仍需人工确认再使用。
                </footer>
            </div>
        </>
    );
}
