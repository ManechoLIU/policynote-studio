import type { Page } from './Sidebar';
import '../styles/mingya-subnav.css';

interface SubNavProps {
  current: Page;
  onNavigate: (page: Page) => void;
}

const TOOLS: { page: Page; label: string }[] = [
  { page: 'discovery', label: '外部选题' },
  { page: 'trends', label: '热点雷达' },
  { page: 'ideas', label: '选题库' },
  { page: 'calendar', label: '内容日历' },
  { page: 'publish', label: '发布中心' },
  { page: 'breakdown', label: '爆款拆解' },
];

export default function SubNav({ current, onNavigate }: SubNavProps) {
  return (
    <nav className="subnav reference-subnav" aria-label="创作工具导航">
      <div className="subnav-tabs">
        {TOOLS.map(({ page, label }) => (
          <button key={page} className={`subnav-tab ${current === page ? 'active' : ''}`}
            aria-current={current === page ? 'page' : undefined}
            onClick={() => onNavigate(page)}>
            {label}{current === page && <span className="subnav-active-rule" aria-hidden="true" />}
          </button>
        ))}
      </div>
    </nav>
  );
}
