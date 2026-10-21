import { forwardRef, useCallback, useEffect, useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { webRequest } from '../../services/webClient';
import './index.css';

type EpisodeStep = { label: string; kind: string; detail: string };
type EpisodeNode = {
  episode_id: string;
  turn_id: string;
  steps: EpisodeStep[];
  objective: string | null;
  outcome: string | null;
};
type EpisodesPayload = { session_id: string; episodes: EpisodeNode[] };

type ExperienceRow = {
  id: string;
  title: string;
  content: string;
  scope_level: string;
  status: string;
  evidence_count: number;
  confidence: number | null;
  use_count: number;
  source_episode_ids: string[];
  target_hints: string[];
  injection_count: number;
};
type ExperiencesPayload = { experiences: ExperienceRow[] };

type AttributionRow = {
  experience_id: string;
  experience_title: string;
  injection_count: number;
  runs: string[];
};
type AttributionPayload = {
  total_experiences: number;
  total_injections: number;
  per_experience: AttributionRow[];
};

type TabKey = 'overview' | 'episodes' | 'experiences' | 'attribution';

export type ExperienceMemoryPanelHandle = { refresh: () => void };

const SCOPE_RANK: Record<string, number> = { user: 4, project: 3, tenant: 2, global: 1 };

export const ExperienceMemoryPanel = forwardRef<ExperienceMemoryPanelHandle>(
  function ExperienceMemoryPanel(_props, ref) {
    const { t } = useTranslation('common');
    const [tab, setTab] = useState<TabKey>('overview');
    const [experiences, setExperiences] = useState<ExperienceRow[]>([]);
    const [attribution, setAttribution] = useState<AttributionPayload | null>(null);
    const [sessionId, setSessionId] = useState('');
    const [episodes, setEpisodes] = useState<EpisodeNode[]>([]);
    const [error, setError] = useState<string | null>(null);
    const [loading, setLoading] = useState(false);

    const loadOverview = useCallback(async () => {
      setLoading(true);
      setError(null);
      try {
        const [exp, attr] = await Promise.all([
          webRequest<ExperiencesPayload>('experiences.list', {}),
          webRequest<AttributionPayload>('injections.attribution', {}),
        ]);
        setExperiences(exp.experiences ?? []);
        setAttribution(attr);
      } catch (e) {
        setError(String(e));
      } finally {
        setLoading(false);
      }
    }, []);

    const loadEpisodes = useCallback(async () => {
      if (!sessionId.trim()) return;
      setLoading(true);
      setError(null);
      try {
        const data = await webRequest<EpisodesPayload>('episodes.list', {
          session_id: sessionId.trim(),
        });
        setEpisodes(data.episodes ?? []);
      } catch (e) {
        setError(String(e));
      } finally {
        setLoading(false);
      }
    }, [sessionId]);

    useEffect(() => {
      loadOverview();
    }, [loadOverview]);

    // Surface a refresh handle to parents (mirrors the SkillGraphPanel pattern).
    useEffect(() => {
      const handle = { refresh: loadOverview };
      if (typeof ref === 'function') ref(handle);
      else if (ref) (ref as React.MutableRefObject<ExperienceMemoryPanelHandle | null>).current = handle;
    }, [loadOverview, ref]);

    const sortedExperiences = useMemo(
      () =>
        [...experiences].sort(
          (a, b) => (SCOPE_RANK[b.scope_level] ?? 0) - (SCOPE_RANK[a.scope_level] ?? 0),
        ),
      [experiences],
    );

    const tabs: { key: TabKey; label: string }[] = [
      { key: 'overview', label: t('experienceMemory.tabs.overview', '概览') },
      { key: 'episodes', label: t('experienceMemory.tabs.episodes', '回合') },
      { key: 'experiences', label: t('experienceMemory.tabs.experiences', '经验') },
      { key: 'attribution', label: t('experienceMemory.tabs.attribution', '归因') },
    ];

    return (
      <div className="experience-memory-panel">
        <div className="emp-tabs" role="tablist">
          {tabs.map((it) => (
            <button
              key={it.key}
              role="tab"
              aria-selected={tab === it.key}
              className={`emp-tab ${tab === it.key ? 'active' : ''}`}
              onClick={() => setTab(it.key)}
            >
              {it.label}
            </button>
          ))}
        </div>
        {error && <div className="emp-error">{error}</div>}
        {loading && <div className="emp-loading">{t('common.loading', '加载中…')}</div>}
        {!loading && tab === 'overview' && attribution && (
          <div className="emp-overview">
            <div className="emp-stat">
              <span className="emp-stat-value">{attribution.total_experiences}</span>
              <span className="emp-stat-label">{t('experienceMemory.stats.experiences', '经验')}</span>
            </div>
            <div className="emp-stat">
              <span className="emp-stat-value">{attribution.total_injections}</span>
              <span className="emp-stat-label">{t('experienceMemory.stats.injections', '注入')}</span>
            </div>
            <div className="emp-stat">
              <span className="emp-stat-value">{experiences.length}</span>
              <span className="emp-stat-label">{t('experienceMemory.stats.stored', '存储')}</span>
            </div>
          </div>
        )}
        {!loading && tab === 'episodes' && (
          <div className="emp-episodes">
            <div className="emp-input-row">
              <input
                className="emp-input"
                placeholder={t('experienceMemory.sessionPlaceholder', '会话 ID')}
                value={sessionId}
                onChange={(e) => setSessionId(e.target.value)}
              />
              <button className="emp-button" onClick={loadEpisodes} disabled={!sessionId.trim()}>
                {t('common.load', '加载')}
              </button>
            </div>
            {episodes.length === 0 ? (
              <div className="emp-empty">{t('experienceMemory.empty', '无回合')}</div>
            ) : (
              <ul className="emp-tree">
                {episodes.map((ep) => (
                  <li key={ep.episode_id} className="emp-episode">
                    <div className="emp-episode-head">
                      <span className="emp-episode-turn">{ep.turn_id}</span>
                      {ep.objective && <span className="emp-episode-obj">{ep.objective}</span>}
                      {ep.outcome && <span className={`emp-episode-outcome emp-outcome-${ep.outcome}`}>{ep.outcome}</span>}
                    </div>
                    <ul className="emp-steps">
                      {ep.steps.map((s, i) => (
                        <li key={`${ep.episode_id}-${i}`} className={`emp-step emp-step-${s.kind}`}>
                          <span className="emp-step-kind">{s.kind}</span>
                          <span className="emp-step-label">{s.label}</span>
                        </li>
                      ))}
                    </ul>
                  </li>
                ))}
              </ul>
            )}
          </div>
        )}
        {!loading && tab === 'experiences' && (
          <table className="emp-table">
            <thead>
              <tr>
                <th>{t('experienceMemory.cols.content', '内容')}</th>
                <th>{t('experienceMemory.cols.scope', '范围')}</th>
                <th>{t('experienceMemory.cols.status', '状态')}</th>
                <th>{t('experienceMemory.cols.evidence', '证据')}</th>
                <th>{t('experienceMemory.cols.injections', '注入')}</th>
              </tr>
            </thead>
            <tbody>
              {sortedExperiences.length === 0 ? (
                <tr><td colSpan={5} className="emp-empty">{t('experienceMemory.empty', '无经验')}</td></tr>
              ) : (
                sortedExperiences.map((r) => (
                  <tr key={r.id}>
                    <td>{r.content}</td>
                    <td>{r.scope_level}</td>
                    <td>{r.status}</td>
                    <td>{r.evidence_count}</td>
                    <td>{r.injection_count}</td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        )}
        {!loading && tab === 'attribution' && attribution && (
          <table className="emp-table">
            <thead>
              <tr>
                <th>{t('experienceMemory.cols.experience', '经验')}</th>
                <th>{t('experienceMemory.cols.injections', '注入')}</th>
                <th>{t('experienceMemory.cols.runs', '运行')}</th>
              </tr>
            </thead>
            <tbody>
              {attribution.per_experience.length === 0 ? (
                <tr><td colSpan={3} className="emp-empty">{t('experienceMemory.empty', '无归因')}</td></tr>
              ) : (
                attribution.per_experience.map((r) => (
                  <tr key={r.experience_id}>
                    <td>{r.experience_title}</td>
                    <td>{r.injection_count}</td>
                    <td>{r.runs.join(', ')}</td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        )}
      </div>
    );
  },
);
