export type Json =
  | string
  | number
  | boolean
  | null
  | { [key: string]: Json | undefined }
  | Json[]

export type Database = {
  // Allows to automatically instantiate createClient with right options
  // instead of createClient<Database, { PostgrestVersion: 'XX' }>(URL, KEY)
  __InternalSupabase: {
    PostgrestVersion: "14.18"
  }
  public: {
    Tables: {
      bookings: {
        Row: {
          created_at: string
          guest_name: string
          id: string
          idempotency_key: string
          party_size: number
          restaurant_id: string
          slot: unknown
          status: string
          table_id: string
          user_id: string
        }
        Insert: {
          created_at?: string
          guest_name: string
          id?: string
          idempotency_key: string
          party_size: number
          restaurant_id: string
          slot: unknown
          status?: string
          table_id: string
          user_id: string
        }
        Update: {
          created_at?: string
          guest_name?: string
          id?: string
          idempotency_key?: string
          party_size?: number
          restaurant_id?: string
          slot?: unknown
          status?: string
          table_id?: string
          user_id?: string
        }
        Relationships: [
          {
            foreignKeyName: "bookings_restaurant_id_fkey"
            columns: ["restaurant_id"]
            isOneToOne: false
            referencedRelation: "restaurants"
            referencedColumns: ["id"]
          },
          {
            foreignKeyName: "bookings_table_id_fkey"
            columns: ["table_id"]
            isOneToOne: false
            referencedRelation: "dining_tables"
            referencedColumns: ["id"]
          },
        ]
      }
      cost_entries: {
        Row: {
          created_at: string
          id: string
          minutes: number
          seat: string
          stage_number: number
          tokens: number
          usd: number
        }
        Insert: {
          created_at?: string
          id?: string
          minutes?: number
          seat: string
          stage_number: number
          tokens?: number
          usd?: number
        }
        Update: {
          created_at?: string
          id?: string
          minutes?: number
          seat?: string
          stage_number?: number
          tokens?: number
          usd?: number
        }
        Relationships: []
      }
      dining_tables: {
        Row: {
          id: string
          label: string
          restaurant_id: string
          seats: number
        }
        Insert: {
          id?: string
          label: string
          restaurant_id: string
          seats: number
        }
        Update: {
          id?: string
          label?: string
          restaurant_id?: string
          seats?: number
        }
        Relationships: [
          {
            foreignKeyName: "dining_tables_restaurant_id_fkey"
            columns: ["restaurant_id"]
            isOneToOne: false
            referencedRelation: "restaurants"
            referencedColumns: ["id"]
          },
        ]
      }
      factory_stages: {
        Row: {
          id: string
          number: number
          status: string
          summary: string
          title: string
        }
        Insert: {
          id?: string
          number: number
          status?: string
          summary?: string
          title: string
        }
        Update: {
          id?: string
          number?: number
          status?: string
          summary?: string
          title?: string
        }
        Relationships: []
      }
      handoffs: {
        Row: {
          created_at: string
          from_seat: string
          id: string
          kind: string
          result: string
          stage_number: number
          summary: string
          to_seat: string
        }
        Insert: {
          created_at?: string
          from_seat: string
          id?: string
          kind: string
          result?: string
          stage_number: number
          summary: string
          to_seat: string
        }
        Update: {
          created_at?: string
          from_seat?: string
          id?: string
          kind?: string
          result?: string
          stage_number?: number
          summary?: string
          to_seat?: string
        }
        Relationships: []
      }
      recovery_events: {
        Row: {
          caught_by: string
          created_at: string
          fix: string
          id: string
          problem: string
          stage_number: number
        }
        Insert: {
          caught_by: string
          created_at?: string
          fix: string
          id?: string
          problem: string
          stage_number: number
        }
        Update: {
          caught_by?: string
          created_at?: string
          fix?: string
          id?: string
          problem?: string
          stage_number?: number
        }
        Relationships: []
      }
      restaurants: {
        Row: {
          city: string
          closes_at: string
          cuisine: string
          description: string
          id: string
          name: string
          opens_at: string
          slot_minutes: number
          slug: string
          timezone: string
        }
        Insert: {
          city: string
          closes_at?: string
          cuisine: string
          description?: string
          id?: string
          name: string
          opens_at?: string
          slot_minutes?: number
          slug: string
          timezone: string
        }
        Update: {
          city?: string
          closes_at?: string
          cuisine?: string
          description?: string
          id?: string
          name?: string
          opens_at?: string
          slot_minutes?: number
          slug?: string
          timezone?: string
        }
        Relationships: []
      }
      seats: {
        Row: {
          id: string
          mandate: string
          model: string
          name: string
          role: string
          sort: number
        }
        Insert: {
          id?: string
          mandate: string
          model?: string
          name: string
          role: string
          sort?: number
        }
        Update: {
          id?: string
          mandate?: string
          model?: string
          name?: string
          role?: string
          sort?: number
        }
        Relationships: []
      }
      stress_runs: {
        Row: {
          attempts: number
          confirmed: number
          conflicts: number
          created_at: string
          duration_ms: number
          errors: number
          id: string
          passed: boolean
          replayed: number
        }
        Insert: {
          attempts: number
          confirmed: number
          conflicts: number
          created_at?: string
          duration_ms: number
          errors: number
          id?: string
          passed: boolean
          replayed: number
        }
        Update: {
          attempts?: number
          confirmed?: number
          conflicts?: number
          created_at?: string
          duration_ms?: number
          errors?: number
          id?: string
          passed?: boolean
          replayed?: number
        }
        Relationships: []
      }
      user_roles: {
        Row: {
          id: string
          role: Database["public"]["Enums"]["app_role"]
          user_id: string
        }
        Insert: {
          id?: string
          role: Database["public"]["Enums"]["app_role"]
          user_id: string
        }
        Update: {
          id?: string
          role?: Database["public"]["Enums"]["app_role"]
          user_id?: string
        }
        Relationships: []
      }
    }
    Views: {
      [_ in never]: never
    }
    Functions: {
      available_slots: {
        Args: { _date: string; _party: number; _restaurant_id: string }
        Returns: {
          free_tables: number
          starts_at: string
        }[]
      }
      book_table: {
        Args: {
          _guest_name: string
          _idempotency_key: string
          _party: number
          _restaurant_id: string
          _starts_at: string
          _table_id?: string
        }
        Returns: Json
      }
      cancel_booking: { Args: { _booking_id: string }; Returns: boolean }
      has_role: {
        Args: {
          _role: Database["public"]["Enums"]["app_role"]
          _user_id: string
        }
        Returns: boolean
      }
    }
    Enums: {
      app_role: "admin" | "staff" | "user"
    }
    CompositeTypes: {
      [_ in never]: never
    }
  }
}

type DatabaseWithoutInternals = Omit<Database, "__InternalSupabase">

type DefaultSchema = DatabaseWithoutInternals[Extract<keyof Database, "public">]

export type Tables<
  DefaultSchemaTableNameOrOptions extends
    | keyof (DefaultSchema["Tables"] & DefaultSchema["Views"])
    | { schema: keyof DatabaseWithoutInternals },
  TableName extends (DefaultSchemaTableNameOrOptions extends {
    schema: keyof DatabaseWithoutInternals
  }
    ? keyof (DatabaseWithoutInternals[DefaultSchemaTableNameOrOptions["schema"]]["Tables"] &
        DatabaseWithoutInternals[DefaultSchemaTableNameOrOptions["schema"]]["Views"])
    : never) = never,
> = DefaultSchemaTableNameOrOptions extends {
  schema: keyof DatabaseWithoutInternals
}
  ? (DatabaseWithoutInternals[DefaultSchemaTableNameOrOptions["schema"]]["Tables"] &
      DatabaseWithoutInternals[DefaultSchemaTableNameOrOptions["schema"]]["Views"])[TableName] extends {
      Row: infer R
    }
    ? R
    : never
  : DefaultSchemaTableNameOrOptions extends keyof (DefaultSchema["Tables"] &
        DefaultSchema["Views"])
    ? (DefaultSchema["Tables"] &
        DefaultSchema["Views"])[DefaultSchemaTableNameOrOptions] extends {
        Row: infer R
      }
      ? R
      : never
    : never

export type TablesInsert<
  DefaultSchemaTableNameOrOptions extends
    | keyof DefaultSchema["Tables"]
    | { schema: keyof DatabaseWithoutInternals },
  TableName extends (DefaultSchemaTableNameOrOptions extends {
    schema: keyof DatabaseWithoutInternals
  }
    ? keyof DatabaseWithoutInternals[DefaultSchemaTableNameOrOptions["schema"]]["Tables"]
    : never) = never,
> = DefaultSchemaTableNameOrOptions extends {
  schema: keyof DatabaseWithoutInternals
}
  ? DatabaseWithoutInternals[DefaultSchemaTableNameOrOptions["schema"]]["Tables"][TableName] extends {
      Insert: infer I
    }
    ? I
    : never
  : DefaultSchemaTableNameOrOptions extends keyof DefaultSchema["Tables"]
    ? DefaultSchema["Tables"][DefaultSchemaTableNameOrOptions] extends {
        Insert: infer I
      }
      ? I
      : never
    : never

export type TablesUpdate<
  DefaultSchemaTableNameOrOptions extends
    | keyof DefaultSchema["Tables"]
    | { schema: keyof DatabaseWithoutInternals },
  TableName extends (DefaultSchemaTableNameOrOptions extends {
    schema: keyof DatabaseWithoutInternals
  }
    ? keyof DatabaseWithoutInternals[DefaultSchemaTableNameOrOptions["schema"]]["Tables"]
    : never) = never,
> = DefaultSchemaTableNameOrOptions extends {
  schema: keyof DatabaseWithoutInternals
}
  ? DatabaseWithoutInternals[DefaultSchemaTableNameOrOptions["schema"]]["Tables"][TableName] extends {
      Update: infer U
    }
    ? U
    : never
  : DefaultSchemaTableNameOrOptions extends keyof DefaultSchema["Tables"]
    ? DefaultSchema["Tables"][DefaultSchemaTableNameOrOptions] extends {
        Update: infer U
      }
      ? U
      : never
    : never

export type Enums<
  DefaultSchemaEnumNameOrOptions extends
    | keyof DefaultSchema["Enums"]
    | { schema: keyof DatabaseWithoutInternals },
  EnumName extends (DefaultSchemaEnumNameOrOptions extends {
    schema: keyof DatabaseWithoutInternals
  }
    ? keyof DatabaseWithoutInternals[DefaultSchemaEnumNameOrOptions["schema"]]["Enums"]
    : never) = never,
> = DefaultSchemaEnumNameOrOptions extends {
  schema: keyof DatabaseWithoutInternals
}
  ? DatabaseWithoutInternals[DefaultSchemaEnumNameOrOptions["schema"]]["Enums"][EnumName]
  : DefaultSchemaEnumNameOrOptions extends keyof DefaultSchema["Enums"]
    ? DefaultSchema["Enums"][DefaultSchemaEnumNameOrOptions]
    : never

export type CompositeTypes<
  PublicCompositeTypeNameOrOptions extends
    | keyof DefaultSchema["CompositeTypes"]
    | { schema: keyof DatabaseWithoutInternals },
  CompositeTypeName extends (PublicCompositeTypeNameOrOptions extends {
    schema: keyof DatabaseWithoutInternals
  }
    ? keyof DatabaseWithoutInternals[PublicCompositeTypeNameOrOptions["schema"]]["CompositeTypes"]
    : never) = never,
> = PublicCompositeTypeNameOrOptions extends {
  schema: keyof DatabaseWithoutInternals
}
  ? DatabaseWithoutInternals[PublicCompositeTypeNameOrOptions["schema"]]["CompositeTypes"][CompositeTypeName]
  : PublicCompositeTypeNameOrOptions extends keyof DefaultSchema["CompositeTypes"]
    ? DefaultSchema["CompositeTypes"][PublicCompositeTypeNameOrOptions]
    : never

export const Constants = {
  public: {
    Enums: {
      app_role: ["admin", "staff", "user"],
    },
  },
} as const
