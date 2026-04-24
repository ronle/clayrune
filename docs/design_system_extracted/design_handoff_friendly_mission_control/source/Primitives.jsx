// Shared primitives for Friendly Mission Control.

function FcChip({ status, children }) {
  const dot = { working: 'green', asking: 'amber', stuck: 'red', done: 'green', idle: 'grey' }[status] || 'grey';
  const pulse = status === 'working';
  return (
    <span className={'fc-chip ' + (pulse ? 'fc-pulse ' : '')}>
      <span className={'fc-dot ' + dot}></span>
      {children}
    </span>
  );
}

function statusLabel(status, voice) {
  const c = {
    working: { casual: 'Working on it', pro: 'In progress' },
    asking:  { casual: 'Needs you',     pro: 'Awaiting input' },
    stuck:   { casual: 'Stuck',         pro: 'Blocked' },
    done:    { casual: 'All done',      pro: 'Completed' },
    idle:    { casual: 'Resting',       pro: 'Idle' },
  };
  return (c[status] || c.idle)[voice];
}

function Avatar({ emoji }) {
  return <div className="fc-pc-avatar">{emoji}</div>;
}

function Progress({ done, total, color }) {
  if (!total) return null;
  const pct = Math.max(4, Math.round((done / total) * 100));
  return (
    <div className={'fc-progress ' + (color || '')}>
      <div className="fc-progress-fill" style={{ width: pct + '%' }}></div>
    </div>
  );
}

function Sidebar({ active, onNav, voice, variant }) {
  const projects = window.FC_ASSISTANTS;
  const nav = [
    { id: 'home',      icon: '🏠', label: voice === 'pro' ? 'Home' : 'Home' },
    { id: 'team',      icon: '👥', label: voice === 'pro' ? 'Team'      : 'My team' },
    { id: 'calendar',  icon: '📅', label: voice === 'pro' ? 'Schedule'  : 'Up next' },
    { id: 'inbox',     icon: '📬', label: voice === 'pro' ? 'Activity'  : 'What happened' },
    { id: 'settings',  icon: '⚙️', label: 'Settings' },
  ];
  return (
    <aside className="fc-sidebar">
      <div className="fc-brand">
        <div className="fc-brand-mark">M</div>
        <div className="fc-brand-name">Mission Control</div>
      </div>
      {nav.map(n => (
        <div key={n.id}
             className={'fc-nav-item' + (active === n.id ? ' active' : '')}
             onClick={() => onNav(n.id)}>
          <span className="fc-nav-icon">{n.icon}</span>
          {n.label}
        </div>
      ))}
      <div className="fc-sidebar-heading">{voice === 'pro' ? 'Projects' : 'Running for you'}</div>
      {projects.slice(0, 5).map(p => (
        <div key={p.id} className="fc-sidebar-item">
          <span className="fc-mini-dot" style={{ background: `var(--${p.dot === 'grey' ? 'ink-faint' : p.dot})` }}></span>
          <span style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{p.project}</span>
        </div>
      ))}
    </aside>
  );
}

function GreetingHeader({ voice, variant, onNew }) {
  const h = voice === 'pro' ? 'Overview' : 'Good afternoon, Jess';
  const eyebrow = voice === 'pro'
    ? { home: 'Dashboard', chat: 'Assistant', today: 'Today' }[variant]
    : { home: 'Here\'s what your team is up to', chat: 'Ask for anything', today: 'Your day at a glance' }[variant];
  return (
    <header className="fc-main-header">
      <div className="fc-greeting">
        <div className="fc-eyebrow">{eyebrow}</div>
        <h1 className="fc-display">{h}</h1>
      </div>
      <div className="fc-actions">
        <button className="fc-btn ghost small">{voice === 'pro' ? 'Team settings' : 'Team'}</button>
        <button className="fc-btn primary small" onClick={onNew}>+ {voice === 'pro' ? 'New project' : 'New project'}</button>
      </div>
    </header>
  );
}

Object.assign(window, { FcChip, statusLabel, Avatar, Progress, Sidebar, GreetingHeader });
