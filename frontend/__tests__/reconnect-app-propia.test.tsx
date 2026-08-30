/**
 * El modal de reconectar tiene que poder declarar la app de Meta que emitió el
 * token.
 *
 * El backend y `api.reconnectAdAccount` ya aceptaban `app_id`/`app_secret`; el
 * modal no los pedía. Y era el único flujo por el que se pasa cuando la cuenta
 * aparece vencida: se pegaba el mismo token de siempre, la cuenta volvía a
 * ACTIVE, y a las 08:00 del día siguiente `debug_token` volvía a preguntar con
 * la app de la agencia por un token que no emitió y la marcaba EXPIRED otra vez.
 */
import React from "react";
import { render, screen, fireEvent, waitFor, within } from "@testing-library/react";
import SettingsPage from "../app/(dashboard)/settings/page";
import { api } from "@/lib/api";

const CUENTA_VENCIDA = {
  id: "acc-its",
  platform: "META",
  external_account_id: "act_its",
  name: "ITS Institud",
  status: "EXPIRED",
  spend_cap: 0,
  amount_spent: 0,
  created_at: "2026-01-01T00:00:00",
  last_sync_error_code: 190,
  app_id: null,
  token_expires_at: null,
};

const CUENTA_PERMANENTE = {
  ...CUENTA_VENCIDA,
  id: "acc-perm",
  name: "Cuenta Permanente",
  status: "ACTIVE",
  last_sync_error_code: null,
  app_id: "1331495631882896",
  token_expires_at: null,
};

jest.mock("@/lib/api", () => {
  const vacio = () => jest.fn().mockResolvedValue([]);
  return {
    api: {
      getOrg: jest.fn(),
      getAdAccounts: jest.fn(),
      getMyPages: vacio(),
      getWhatsAppNumbers: vacio(),
      getBusinessPolicies: jest.fn().mockResolvedValue({ available_industries: [] }),
      getMe: jest.fn().mockResolvedValue({ full_name: "Luis", email: "luis@x.com" }),
      getOrgMembers: vacio(),
      getOrgInvitations: vacio(),
      getMemberAdAccounts: jest.fn().mockResolvedValue({ granted_ids: [] }),
      getCampaignRulesConfig: jest.fn().mockResolvedValue({ rules: {} }),
      getAIThresholdsConfig: jest.fn().mockResolvedValue({ thresholds: {} }),
      getAiModels: jest.fn().mockResolvedValue({
        models: [], active_provider: "openai", credential_ok: true, effective_model: null,
      }),
      reconnectAdAccount: jest.fn().mockResolvedValue({}),
      checkAdAccountToken: jest.fn(),
    },
  };
});

jest.mock("next/navigation", () => ({
  useRouter: () => ({ push: jest.fn() }),
  usePathname: () => "/settings",
}));

/** El formulario de "Conectar Cuenta" usa los mismos placeholders. */
async function modalDeReconexion() {
  const boton = await screen.findByRole("button", { name: /Guardar y Reactivar/i });
  return within(boton.closest("form") as HTMLElement);
}

async function renderSettings(cuentas: unknown[]) {
  (api.getOrg as jest.Mock).mockResolvedValue({
    id: "org", slug: "kibo", name: "Kibo", max_budget: 1000, status: "ACTIVE",
  });
  (api.getAdAccounts as jest.Mock).mockResolvedValue(cuentas);
  render(<SettingsPage />);
  await waitFor(() => expect(api.getAdAccounts).toHaveBeenCalled());
}

describe("Reconectar declarando la app propia de Meta", () => {
  beforeEach(() => jest.clearAllMocks());

  it("manda app_id y app_secret cuando se completan", async () => {
    await renderSettings([CUENTA_VENCIDA]);

    fireEvent.click(await screen.findByRole("button", { name: /Reconectar/i }));

    const modal = await modalDeReconexion();
    fireEvent.change(modal.getByPlaceholderText("EAAB..."), {
      target: { value: "token-de-system-user" },
    });
    fireEvent.change(modal.getByPlaceholderText("1331495631882896"), {
      target: { value: "1331495631882896" },
    });
    fireEvent.change(modal.getByPlaceholderText("Se guarda cifrado"), {
      target: { value: "el-secreto-de-its" },
    });

    fireEvent.click(screen.getByRole("button", { name: /Guardar y Reactivar/i }));

    await waitFor(() =>
      expect(api.reconnectAdAccount).toHaveBeenCalledWith(
        "acc-its", "token-de-system-user", "1331495631882896", "el-secreto-de-its",
      )
    );
  });

  it("los omite cuando quedan vacíos, para no pisar la app ya guardada", async () => {
    await renderSettings([CUENTA_VENCIDA]);

    fireEvent.click(await screen.findByRole("button", { name: /Reconectar/i }));
    const modal = await modalDeReconexion();
    fireEvent.change(modal.getByPlaceholderText("EAAB..."), {
      target: { value: "otro-token" },
    });
    fireEvent.click(screen.getByRole("button", { name: /Guardar y Reactivar/i }));

    await waitFor(() =>
      expect(api.reconnectAdAccount).toHaveBeenCalledWith(
        "acc-its", "otro-token", undefined, undefined,
      )
    );
  });

  it("un token sin caducidad se muestra como permanente, no como 'Vence en 0d'", async () => {
    await renderSettings([CUENTA_PERMANENTE]);
    expect(await screen.findByText(/Token permanente/i)).toBeInTheDocument();
    expect(screen.queryByText(/Vence en/i)).not.toBeInTheDocument();
  });
});
