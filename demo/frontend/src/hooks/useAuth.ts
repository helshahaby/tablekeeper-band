import { useQuery } from "@tanstack/react-query";
import { getMe } from "@/lib/api.functions";

export function useAuth() {
  const q = useQuery({ queryKey: ["me"], queryFn: () => getMe(), staleTime: 30_000 });
  return { user: q.data ?? null, ready: !q.isLoading };
}
