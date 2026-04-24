// Today List layout: calm grouped list.

function TodayListLayout({ voice, onNew }) {
  const byId = Object.fromEntries(window.FC_ASSISTANTS.map(a => [a.id, a]));
  const groups = window.FC_TODAY_GROUPS
    .map(g => ({ ...g, items: g.ids.map(id => byId[id]).filter(Boolean) }))
    .filter(g => g.items.length > 0);

  return (
    <div>
      <window.GreetingHeader voice={voice} variant="today" onNew={onNew} />
      {groups.map(g => (
        <section key={g.id} className="fc-today-group">
          <div className="fc-today-title">
            <h2>{voice === 'pro' ? g.titlePro : g.titleCasual}</h2>
            <span className="fc-count">{g.items.length}</span>
          </div>
          <div className="fc-today-list">
            {g.items.map(p => (
              <div key={p.id} className="fc-today-row">
                <window.Avatar emoji={p.emoji} />
                <div className="fc-row-main">
                  <div className="fc-row-name">
                    {p.name}
                    <window.FcChip status={p.status}>{window.statusLabel(p.status, voice)}</window.FcChip>
                  </div>
                  <div className="fc-row-summary">{p.summary[voice]}</div>
                </div>
                <div className="fc-row-meta">
                  <span>{p.lastAction}</span>
                  <button className={'fc-btn small ' + ((p.status === 'asking' || p.status === 'stuck') ? 'primary' : 'ghost')}>
                    {p.cta[voice]}
                  </button>
                </div>
              </div>
            ))}
          </div>
        </section>
      ))}
    </div>
  );
}

window.TodayListLayout = TodayListLayout;
