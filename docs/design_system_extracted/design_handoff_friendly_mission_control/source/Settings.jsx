// Settings page — where users change tone, density, voice, accent.

function SettingsPage({ state, updateState }) {
  const accents = [
    { id: 'orange', val: '#e8824a', label: 'Sunset' },
    { id: 'rose',   val: '#d96480', label: 'Rose' },
    { id: 'lilac',  val: '#8a7ce0', label: 'Lilac' },
    { id: 'teal',   val: '#4fa89a', label: 'Lagoon' },
    { id: 'ink',    val: '#2b2f3a', label: 'Ink' },
  ];

  const row = (title, subtitle, children) => (
    <div className="fc-setting-row">
      <div className="fc-setting-meta">
        <div className="fc-setting-title">{title}</div>
        <div className="fc-setting-sub">{subtitle}</div>
      </div>
      <div className="fc-setting-control">{children}</div>
    </div>
  );

  const seg = (key, options) => (
    <div className="fc-seg">
      {options.map(o => (
        <button key={o.val}
                className={state[key] === o.val ? 'active' : ''}
                onClick={() => updateState({ ...state, [key]: o.val })}>
          {o.label}
        </button>
      ))}
    </div>
  );

  return (
    <div>
      <header className="fc-main-header">
        <div className="fc-greeting">
          <div className="fc-eyebrow">Settings</div>
          <h1 className="fc-display">Make it yours</h1>
        </div>
      </header>

      <section className="fc-settings-section">
        <div className="fc-settings-section-title">Appearance</div>
        <div className="fc-settings-panel">
          {row('Theme', 'Darker for late nights, warmer for daytime.',
            seg('tone', [
              { val: 'tone-dark', label: 'Soft dark' },
              { val: 'tone-warm', label: 'Warm' },
            ]))}

          {row('Accent color', 'Used for highlights, buttons, and your active project.',
            <div className="fc-accent-row">
              {accents.map(a => (
                <button key={a.id}
                        className={'fc-accent-pill' + (state.accent === a.val ? ' active' : '')}
                        onClick={() => updateState({ ...state, accent: a.val })}>
                  <span className="fc-accent-swatch" style={{ background: a.val }}></span>
                  {a.label}
                </button>
              ))}
            </div>)}

          {row('Density', 'How much air is between things.',
            seg('density', [
              { val: 'density-cozy',    label: 'Cozy' },
              { val: 'density-compact', label: 'Compact' },
            ]))}
        </div>
      </section>

      <section className="fc-settings-section">
        <div className="fc-settings-section-title">Voice</div>
        <div className="fc-settings-panel">
          {row('Writing style',
            state.voice === 'casual'
              ? 'Your assistants will write like a friend helping you out.'
              : 'Your assistants will write clearly and professionally.',
            seg('voice', [
              { val: 'casual', label: 'Casual' },
              { val: 'pro',    label: 'Professional' },
            ]))}

          <div className="fc-voice-preview">
            <div className="fc-voice-preview-label">Preview</div>
            <div className="fc-voice-preview-msg">
              {state.voice === 'casual'
                ? '“I need a couple of quick answers before I book the venue.”'
                : '“Awaiting input: venue selection requires 2 decisions.”'}
            </div>
          </div>
        </div>
      </section>

      <section className="fc-settings-section">
        <div className="fc-settings-section-title">Help & tour</div>
        <div className="fc-settings-panel">
          {row('Replay the welcome tour',
            'Walk through Mission Control again with the sample project.',
            <button className="fc-btn ghost small" onClick={() => updateState({ ...state, tour: true })}>
              Replay tour
            </button>)}
        </div>
      </section>
    </div>
  );
}

window.SettingsPage = SettingsPage;
