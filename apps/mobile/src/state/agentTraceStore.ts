/**
 * agentTraceStore — Phase 3.3.
 *
 * Tracks which sub-agents are active and what tool they're currently
 * running. Fed by the gateway's `agent_event` / `run_end` events
 * (see services/orchestrator/main.py `_sse()` shape).
 *
 * UI consumers (AgentChips, agent activity log) subscribe to the
 * `useAgentTrace()` hook.
 *
 * Lifecycle per turn:
 *   1. run_start  → mark all known agents as idle, clear last events
 *   2. agent_start{agent="locator_agent"}    → flip to "active"
 *   3. agent_tool{agent="locator_agent",detail.tool="search"} → "running tool"
 *   4. tool_result{agent="locator_agent",detail.tool="search"} → "idle"
 *   5. agent_end{agent="locator_agent"} → back to "idle"
 *   6. run_end → reset everything (small fade in the UI)
 */
import { create } from 'zustand';

import type { AgentTraceStep } from '@/voice/VoiceClient';

export type KnownAgent = 'realestate_agent' | 'locator_agent' | 'audit_agent';

export type AgentState = 'inactive' | 'active' | 'tool';

export interface AgentStatus {
  state: AgentState;
  lastTool?: string;
  lastToolAt?: number;     // monotonic ms; used for "recently active" fades
}

export interface AgentTraceStore {
  byAgent: Record<KnownAgent, AgentStatus>;
  /** Last 20 raw events (debug / activity feed). */
  recent: AgentTraceStep[];
  applyEvent: (ev: AgentTraceStep) => void;
  reset: () => void;
}

const EMPTY: Record<KnownAgent, AgentStatus> = {
  realestate_agent: { state: 'inactive' },
  locator_agent: { state: 'inactive' },
  audit_agent: { state: 'inactive' },
};

const RECENT_MAX = 20;

export const useAgentTrace = create<AgentTraceStore>((set, get) => ({
  byAgent: { ...EMPTY },
  recent: [],
  applyEvent: (ev) => {
    const next = { ...get().byAgent };
    const a = ev.agent as KnownAgent;
    if (a in next) {
      switch (ev.kind) {
        case 'agent_start':
          next[a] = { ...next[a], state: 'active' };
          break;
        case 'agent_tool':
          next[a] = {
            state: 'tool',
            lastTool: (ev.detail?.tool as string | undefined) ?? next[a].lastTool,
            lastToolAt: Date.now(),
          };
          break;
        case 'tool_result':
          next[a] = {
            state: 'active',
            lastTool: (ev.detail?.tool as string | undefined) ?? next[a].lastTool,
            lastToolAt: Date.now(),
          };
          break;
        case 'agent_end':
          next[a] = { ...next[a], state: 'inactive' };
          break;
        case 'run_end':
        case 'text_delta':
        case 'error':
        default:
          break;
      }
    }
    const recent = [ev, ...get().recent].slice(0, RECENT_MAX);
    set({ byAgent: next, recent });
    // On run_end, fade everything back to idle.
    if (ev.kind === 'run_end') {
      setTimeout(() => set({ byAgent: { ...EMPTY } }), 400);
    }
  },
  reset: () => set({ byAgent: { ...EMPTY }, recent: [] }),
}));
