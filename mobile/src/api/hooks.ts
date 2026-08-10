import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { useSession } from "../stores/session";
import { api } from "./client";
import {
  Account,
  Advice,
  BacktestRequest,
  BacktestResponse,
  BrokerageConnection,
  BrokerageLink,
  BrokerageSyncResponse,
  BudgetRuleReport,
  BudgetSettings,
  CandleSeries,
  Category,
  ChatMessage,
  ChatSendResponse,
  ChatSession,
  ChatStatus,
  DashboardSummary,
  DcaPlanRequest,
  DcaPlanResponse,
  DeployAcceptRequest,
  DeployPlan,
  DeployPlanRequest,
  ExchangeResponse,
  Explainer,
  Holding,
  InvestSyncResponse,
  LinkTokenResponse,
  MonthlyReportResponse,
  PlaidCredentialInput,
  PlaidStatus,
  AutoTradeRun,
  AutoTraderConfig,
  CommitteeResult,
  CommitteeSession,
  PaperPortfolio,
  PaperTrade,
  Portfolio,
  WatchlistItem,
  ProjectionRequest,
  ProjectionResponse,
  Proposal,
  ProposedOrder,
  RiskReport,
  Scenario,
  SymbolLookup,
  SyncResponse,
  Target,
  TokenPair,
  Transaction,
} from "./types";

// --- Auth ---
export function useRegister() {
  const setTokens = useSession((s) => s.setTokens);
  return useMutation({
    mutationFn: async (body: { email: string; password: string; full_name?: string }) => {
      const { data } = await api.post<TokenPair>("/auth/register", body);
      return data;
    },
    onSuccess: (data) => setTokens(data.access_token, data.refresh_token),
  });
}

export function useLogin() {
  const setTokens = useSession((s) => s.setTokens);
  return useMutation({
    mutationFn: async (body: { email: string; password: string }) => {
      const { data } = await api.post<TokenPair>("/auth/login", body);
      return data;
    },
    onSuccess: (data) => setTokens(data.access_token, data.refresh_token),
  });
}

// --- Dashboard ---
export function useDashboard() {
  return useQuery({
    queryKey: ["dashboard"],
    queryFn: async () => (await api.get<DashboardSummary>("/dashboard/summary")).data,
  });
}

// --- Accounts ---
export function useAccounts() {
  return useQuery({
    queryKey: ["accounts"],
    queryFn: async () => (await api.get<Account[]>("/accounts")).data,
  });
}

// --- Transactions ---
export function useTransactions() {
  return useQuery({
    queryKey: ["transactions"],
    queryFn: async () =>
      (await api.get<Transaction[]>("/transactions", { params: { limit: 500 } })).data,
  });
}

export function useCategories() {
  return useQuery({
    queryKey: ["categories"],
    queryFn: async () => (await api.get<Category[]>("/categories")).data,
    staleTime: 5 * 60 * 1000,
  });
}

export function useUpdateCategory() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (vars: { transactionId: string; categoryId: string }) => {
      const { data } = await api.patch<Transaction>(
        `/transactions/${vars.transactionId}/category`,
        { category_id: vars.categoryId },
      );
      return data;
    },
    onSuccess: () => {
      // The correction affects lists, dashboard aggregates and report buckets.
      qc.invalidateQueries({ queryKey: ["transactions"] });
      qc.invalidateQueries({ queryKey: ["dashboard"] });
      qc.invalidateQueries({ queryKey: ["reports"] });
    },
  });
}

// --- Custom categories ---
// Deliberately not named useUpdateCategory — that name already recategorizes a
// *transaction*; this edits a category's own definition (name/color/bucket).
export function useCreateCategory() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (body: {
      name: string;
      group: "income" | "expense";
      color?: string;
      budget_bucket?: "needs" | "wants";
    }) => (await api.post<Category>("/categories", body)).data,
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["categories"] });
      qc.invalidateQueries({ queryKey: ["transactions"] });
      qc.invalidateQueries({ queryKey: ["dashboard"] });
      qc.invalidateQueries({ queryKey: ["reports"] });
    },
  });
}

export function useUpdateCategoryDefinition() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (vars: {
      categoryId: string;
      name?: string;
      color?: string;
      budget_bucket?: "needs" | "wants";
    }) => {
      const { categoryId, ...body } = vars;
      return (await api.patch<Category>(`/categories/${categoryId}`, body)).data;
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["categories"] });
      qc.invalidateQueries({ queryKey: ["transactions"] });
      qc.invalidateQueries({ queryKey: ["dashboard"] });
      // A bucket change moves money between needs and wants.
      qc.invalidateQueries({ queryKey: ["reports"] });
    },
  });
}

export function useDeleteCategory() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (categoryId: string) => {
      await api.delete(`/categories/${categoryId}`);
    },
    onSuccess: () => {
      // A delete reassigns transactions to Uncategorized.
      qc.invalidateQueries({ queryKey: ["categories"] });
      qc.invalidateQueries({ queryKey: ["transactions"] });
      qc.invalidateQueries({ queryKey: ["dashboard"] });
      qc.invalidateQueries({ queryKey: ["reports"] });
    },
  });
}

// --- Reports / 50-30-20 ---
export function useMonthlyReport(months = 6) {
  return useQuery({
    queryKey: ["reports", "monthly", months],
    queryFn: async () =>
      (await api.get<MonthlyReportResponse>("/reports/monthly", { params: { months } }))
        .data,
  });
}

export function useBudgetRule(month: string | undefined) {
  return useQuery({
    queryKey: ["reports", "budget-rule", month],
    enabled: !!month,
    queryFn: async () =>
      (await api.get<BudgetRuleReport>("/reports/budget-rule", { params: { month } }))
        .data,
  });
}

// --- Advice ---
export function useAdvice(month: string | undefined) {
  return useQuery({
    queryKey: ["advice", month],
    enabled: !!month,
    queryFn: async () =>
      (await api.get<Advice>("/advice/monthly", { params: { month } })).data,
  });
}

export function useGenerateAdvice() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (vars: { month: string; force?: boolean }) =>
      (
        await api.post<Advice>("/advice/monthly", null, {
          params: { month: vars.month, force: vars.force ?? false },
          // Generation still outlives the client's 20s default.
          timeout: 60_000,
        })
      ).data,
    onSuccess: (data) => qc.setQueryData(["advice", data.month], data),
  });
}

// --- Budget settings (income override) ---
export function useBudgetSettings() {
  return useQuery({
    queryKey: ["budget-settings"],
    queryFn: async () => (await api.get<BudgetSettings>("/settings/budget")).data,
  });
}

export function useSetBudgetSettings() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (body: BudgetSettings) =>
      (await api.put<BudgetSettings>("/settings/budget", body)).data,
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["budget-settings"] });
      qc.invalidateQueries({ queryKey: ["reports"] });
    },
  });
}

// --- Invest: AI committee ---
export function useAnalyzeStock() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (symbol: string) =>
      (
        await api.post<CommitteeResult>(
          "/invest/committee/analyze",
          { symbol },
          { timeout: 60_000 },
        )
      ).data,
    onSuccess: () => qc.invalidateQueries({ queryKey: ["invest", "committee"] }),
  });
}

export function useCommitteeSessions() {
  return useQuery({
    queryKey: ["invest", "committee", "sessions"],
    queryFn: async () =>
      (await api.get<CommitteeSession[]>("/invest/committee/sessions")).data,
  });
}

// --- Invest: paper trading ---
export function usePaperPortfolio() {
  return useQuery({
    queryKey: ["invest", "paper", "portfolio"],
    queryFn: async () => (await api.get<PaperPortfolio>("/invest/paper/portfolio")).data,
  });
}

export function usePaperTrade(side: "buy" | "sell") {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (body: { symbol: string; qty: number }) =>
      (await api.post<PaperTrade>(`/invest/paper/${side}`, body, { timeout: 30_000 })).data,
    onSuccess: () => qc.invalidateQueries({ queryKey: ["invest", "paper"] }),
  });
}

export function usePaperTrades() {
  return useQuery({
    queryKey: ["invest", "paper", "trades"],
    queryFn: async () => (await api.get<PaperTrade[]>("/invest/paper/trades")).data,
  });
}

// --- Invest: auto-trader ---
export function useAutoTraderConfig() {
  return useQuery({
    queryKey: ["invest", "autotrader", "config"],
    queryFn: async () =>
      (await api.get<AutoTraderConfig>("/invest/autotrader/config")).data,
  });
}

export function useUpdateAutoTrader() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (body: Partial<AutoTraderConfig>) =>
      (await api.put<AutoTraderConfig>("/invest/autotrader/config", body)).data,
    onSuccess: () => qc.invalidateQueries({ queryKey: ["invest", "autotrader"] }),
  });
}

export function useRunAutoTrader() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async () =>
      (await api.post<AutoTradeRun>("/invest/autotrader/run", null, { timeout: 120_000 }))
        .data,
    onSuccess: () => qc.invalidateQueries({ queryKey: ["invest"] }),
  });
}

export function useAutoTraderRuns() {
  return useQuery({
    queryKey: ["invest", "autotrader", "runs"],
    queryFn: async () => (await api.get<AutoTradeRun[]>("/invest/autotrader/runs")).data,
  });
}

// --- Invest: candles ---
/**
 * OHLC candles for a ticker. Polls while the screen is open so the chart keeps
 * updating — the upstream feed is free and therefore delayed, so polling faster
 * than a minute would just re-fetch the same bars.
 */
export function useCandles(symbol: string, interval: string, range: string) {
  return useQuery({
    queryKey: ["invest", "candles", symbol, interval, range],
    enabled: !!symbol,
    refetchInterval: 60_000,
    queryFn: async () =>
      (
        await api.get<CandleSeries>("/invest/candles", {
          params: { symbol, interval, range },
        })
      ).data,
  });
}

// --- Invest: watchlist ---
export function useWatchlist() {
  return useQuery({
    queryKey: ["invest", "watchlist"],
    queryFn: async () => (await api.get<WatchlistItem[]>("/invest/watchlist")).data,
  });
}

export function useAddWatch() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (symbol: string) =>
      (await api.post<WatchlistItem>("/invest/watchlist", { symbol }, { timeout: 30_000 }))
        .data,
    onSuccess: () => qc.invalidateQueries({ queryKey: ["invest", "watchlist"] }),
  });
}

export function useRemoveWatch() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (id: string) => {
      await api.delete(`/invest/watchlist/${id}`);
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: ["invest", "watchlist"] }),
  });
}

// --- Invest: holdings ---
export function useHoldings() {
  return useQuery({
    queryKey: ["invest", "holdings"],
    queryFn: async () => (await api.get<Holding[]>("/invest/holdings")).data,
  });
}

export function useLookupSymbol() {
  return useMutation({
    mutationFn: async (symbol: string) =>
      (await api.get<SymbolLookup>("/invest/lookup", { params: { symbol } })).data,
  });
}

export function useUpsertHolding() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (body: { symbol: string; qty: number; avg_cost: number }) =>
      (await api.put<Holding>("/invest/holdings", body, { timeout: 60_000 })).data,
    onSuccess: () => qc.invalidateQueries({ queryKey: ["invest"] }),
  });
}

export function useDeleteHolding() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (holdingId: string) => {
      await api.delete(`/invest/holdings/${holdingId}`);
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: ["invest"] }),
  });
}

export function useSetCash() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (cash: number) =>
      (await api.put<{ cash: number }>("/invest/cash", { cash })).data,
    onSuccess: () => qc.invalidateQueries({ queryKey: ["invest"] }),
  });
}

// --- Invest: portfolio + analytics ---
export function usePortfolio() {
  return useQuery({
    queryKey: ["invest", "portfolio"],
    queryFn: async () => (await api.get<Portfolio>("/invest/portfolio")).data,
  });
}

export function useInvestSync() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async () =>
      (await api.post<InvestSyncResponse>("/invest/sync", null, { timeout: 120_000 }))
        .data,
    onSuccess: () => qc.invalidateQueries({ queryKey: ["invest"] }),
  });
}

export function useRiskMetrics(window: "1y" | "3y" | "max") {
  return useQuery({
    queryKey: ["invest", "risk", window],
    queryFn: async () =>
      (await api.get<RiskReport>("/invest/risk", { params: { window } })).data,
  });
}

export function useBacktest() {
  return useMutation({
    mutationFn: async (body: BacktestRequest) =>
      (await api.post<BacktestResponse>("/invest/backtest", body, { timeout: 60_000 }))
        .data,
  });
}

export function useDcaPlan() {
  return useMutation({
    mutationFn: async (body: DcaPlanRequest) =>
      (await api.post<DcaPlanResponse>("/invest/dca/plan", body, { timeout: 60_000 }))
        .data,
  });
}

export function useProjection() {
  return useMutation({
    mutationFn: async (body: ProjectionRequest) =>
      (await api.post<ProjectionResponse>("/invest/projection", body, { timeout: 60_000 }))
        .data,
  });
}

export function useScenarios() {
  return useQuery({
    queryKey: ["invest", "scenarios"],
    queryFn: async () => (await api.get<Scenario[]>("/invest/scenarios")).data,
  });
}

// --- Invest: targets + orders ---
export function useTargets() {
  return useQuery({
    queryKey: ["invest", "targets"],
    queryFn: async () =>
      (await api.get<{ targets: Target[] }>("/invest/targets")).data.targets,
  });
}

export function useSetTargets() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (targets: Target[]) =>
      (await api.put<{ targets: Target[] }>("/invest/targets", targets)).data,
    onSuccess: () => qc.invalidateQueries({ queryKey: ["invest", "targets"] }),
  });
}

export function useProposeRebalance() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (body: { drift_threshold?: number }) =>
      (await api.post<Proposal>("/invest/rebalance/propose", body)).data,
    onSuccess: () => qc.invalidateQueries({ queryKey: ["invest", "proposed"] }),
  });
}

// --- deploy cash ---
/**
 * The AI committee deliberates on each shortlisted ticker — one request per
 * ticker, run concurrently — so this outruns the client's 20s default. The
 * ceiling is the server's own gateway timeout, not the model.
 */
export function useDeployPlan() {
  return useMutation({
    mutationFn: async (body: DeployPlanRequest) =>
      (await api.post<DeployPlan>("/invest/deploy/propose", body, { timeout: 60_000 }))
        .data,
  });
}

export function useAcceptDeployPlan() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (body: DeployAcceptRequest) =>
      (await api.post<Proposal>("/invest/deploy/accept", body)).data,
    onSuccess: () => qc.invalidateQueries({ queryKey: ["invest", "proposed"] }),
  });
}

export function useProposedOrders() {
  return useQuery({
    queryKey: ["invest", "proposed"],
    queryFn: async () =>
      (await api.get<ProposedOrder[]>("/invest/orders/proposed")).data,
  });
}

/** The user placed this trade at their own broker — update holdings to match. */
export function useMarkExecuted() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (vars: { orderId: string; fill_price?: number }) =>
      (
        await api.post<ProposedOrder>(
          `/invest/orders/proposed/${vars.orderId}/executed`,
          { fill_price: vars.fill_price ?? null },
        )
      ).data,
    onSuccess: () => qc.invalidateQueries({ queryKey: ["invest"] }),
  });
}

export function useRejectOrder() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (orderId: string) =>
      (await api.post<ProposedOrder>(`/invest/orders/proposed/${orderId}/reject`)).data,
    onSuccess: () => qc.invalidateQueries({ queryKey: ["invest", "proposed"] }),
  });
}

// --- Brokerage (SnapTrade): link, sync, live execution ---
export function useBrokerageConnections() {
  return useQuery({
    queryKey: ["brokerage", "connections"],
    queryFn: async () =>
      (await api.get<BrokerageConnection[]>("/brokerage/connections")).data,
  });
}

/** Returns the SnapTrade portal URL — open it, then call useCompleteBrokerageLink.
 * ``redirectUri`` is the app's deep link the portal returns to on "Done". */
export function useStartBrokerageLink() {
  return useMutation({
    mutationFn: async (redirectUri?: string) =>
      (await api.post<BrokerageLink>("/brokerage/link", { redirect_uri: redirectUri }))
        .data,
  });
}

export function useCompleteBrokerageLink() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async () =>
      (await api.post<BrokerageConnection[]>("/brokerage/link/complete")).data,
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["brokerage"] });
      qc.invalidateQueries({ queryKey: ["invest"] });
    },
  });
}

export function useUnlinkBrokerage() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async () => (await api.delete("/brokerage/connections")).data,
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["brokerage"] });
      qc.invalidateQueries({ queryKey: ["invest"] });
    },
  });
}

/** Mirror real broker positions into holdings. The broker is authoritative. */
export function useSyncBrokerage() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async () =>
      (await api.post<BrokerageSyncResponse>("/brokerage/sync")).data,
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["brokerage"] });
      qc.invalidateQueries({ queryKey: ["invest"] });
    },
  });
}

/**
 * Approve a proposed order and submit it to the linked broker — this spends
 * real money. Callers must confirm with the user first.
 */
export function useApproveAndSubmit() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (orderId: string) =>
      (await api.post<ProposedOrder>(`/brokerage/orders/${orderId}/approve`)).data,
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["brokerage"] });
      qc.invalidateQueries({ queryKey: ["invest"] });
    },
  });
}

/** Poll the broker for a submitted order and settle holdings if it filled. */
export function useRefreshOrder() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (orderId: string) =>
      (await api.post<ProposedOrder>(`/brokerage/orders/${orderId}/refresh`)).data,
    onSuccess: () => qc.invalidateQueries({ queryKey: ["invest"] }),
  });
}

// --- Invest: explainers + AI ---
export function useExplainers() {
  return useQuery({
    queryKey: ["invest", "explainers"],
    queryFn: async () =>
      (await api.get<Record<string, Explainer>>("/invest/explainers")).data,
    staleTime: Infinity,
  });
}

export function useInvestInsight() {
  return useQuery({
    queryKey: ["invest", "insight"],
    queryFn: async () => (await api.get<Advice>("/invest/insight")).data,
  });
}

export function useGenerateInvestInsight() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (vars: { force?: boolean }) =>
      (
        await api.post<Advice>("/invest/insight/generate", null, {
          params: { force: vars.force ?? false },
          timeout: 60_000,
        })
      ).data,
    onSuccess: (data) => qc.setQueryData(["invest", "insight"], data),
  });
}

// --- Plaid ---
export function useCreateLinkToken() {
  return useMutation({
    mutationFn: async () => (await api.post<LinkTokenResponse>("/plaid/link/token")).data,
  });
}

export function useExchangePublicToken() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (body: {
      public_token: string;
      institution_id?: string;
      institution_name?: string;
    }) => (await api.post<ExchangeResponse>("/plaid/link/exchange", body)).data,
    onSuccess: () => {
      qc.invalidateQueries();
    },
  });
}

export function useSync() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async () => (await api.post<SyncResponse>("/plaid/sync")).data,
    onSuccess: () => {
      qc.invalidateQueries();
    },
  });
}

// --- Settings (Plaid credentials) ---
export function usePlaidStatus() {
  return useQuery({
    queryKey: ["plaid-status"],
    queryFn: async () => (await api.get<PlaidStatus>("/settings/plaid")).data,
  });
}

export function useSetPlaidCredentials() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (body: PlaidCredentialInput) =>
      (await api.put<PlaidStatus>("/settings/plaid", body)).data,
    onSuccess: () => qc.invalidateQueries({ queryKey: ["plaid-status"] }),
  });
}

export function useDeletePlaidCredentials() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async () => (await api.delete<PlaidStatus>("/settings/plaid")).data,
    onSuccess: () => qc.invalidateQueries({ queryKey: ["plaid-status"] }),
  });
}

// --- Finance chat ---
export function useChatStatus() {
  return useQuery({
    queryKey: ["chat", "status"],
    queryFn: async () => (await api.get<ChatStatus>("/chat/status")).data,
  });
}

export function useChatSessions() {
  return useQuery({
    queryKey: ["chat", "sessions"],
    queryFn: async () => (await api.get<ChatSession[]>("/chat/sessions")).data,
  });
}

export function useChatMessages(sessionId: string | null) {
  return useQuery({
    queryKey: ["chat", "messages", sessionId],
    enabled: !!sessionId,
    queryFn: async () =>
      (await api.get<ChatMessage[]>(`/chat/sessions/${sessionId}/messages`)).data,
  });
}

export function useSendChatMessage() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (vars: {
      message: string;
      session_id?: string | null;
      include_finances?: boolean;
    }) =>
      (
        await api.post<ChatSendResponse>(
          "/chat/messages",
          {
            message: vars.message,
            session_id: vars.session_id ?? null,
            include_finances: vars.include_finances ?? true,
          },
          // A reply is a full generation; outlive the default 20s.
          { timeout: 60_000 },
        )
      ).data,
    onSuccess: (data) => {
      // Append both turns rather than refetching, so the thread doesn't blink.
      qc.setQueryData<ChatMessage[]>(
        ["chat", "messages", data.session_id],
        (prev) => [...(prev ?? []), data.user_message, data.reply],
      );
      qc.invalidateQueries({ queryKey: ["chat", "sessions"] });
    },
  });
}

export function useDeleteChatSession() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (sessionId: string) => {
      await api.delete(`/chat/sessions/${sessionId}`);
      return sessionId;
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: ["chat", "sessions"] }),
  });
}
