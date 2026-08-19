import React from "react";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import AcceptInvitationPage from "../app/invitations/[token]/page";
import { api } from "@/lib/api";

jest.mock("@/lib/api", () => ({
  api: {
    getInvitationDetails: jest.fn(),
    acceptInvitation: jest.fn(),
  },
}));

const mockPush = jest.fn();
let mockParams = { token: "expired-token" };
jest.mock("next/navigation", () => ({
  useRouter: () => ({
    push: mockPush,
  }),
  useParams: () => mockParams,
}));

describe("AcceptInvitationPage", () => {
  beforeEach(() => {
    jest.clearAllMocks();
  });

  it("renders expired invitation state", async () => {
    mockParams = { token: "expired-token" };
    (api.getInvitationDetails as jest.Mock).mockResolvedValueOnce({
      email: "test@example.com",
      role: "OPERATOR",
      organization_name: "Acme Corp",
      organization_slug: "acme",
      is_expired: true,
      is_accepted: false,
    });

    render(<AcceptInvitationPage />);

    await waitFor(() => {
      expect(screen.getByText(/Invitación expirada/i)).toBeInTheDocument();
    });
  });

  it("renders valid invitation form and submits successfully", async () => {
    (api.getInvitationDetails as jest.Mock).mockResolvedValueOnce({
      email: "newmember@example.com",
      role: "OPERATOR",
      organization_name: "Acme Corp",
      organization_slug: "acme",
      is_expired: false,
      is_accepted: false,
    });

    (api.acceptInvitation as jest.Mock).mockResolvedValueOnce({
      access_token: "mock-jwt-token",
      token_type: "bearer",
      user: {
        id: "usr-1",
        email: "newmember@example.com",
        full_name: "Nuevo Miembro",
        role: "OPERATOR",
        organization_id: "org-1",
      },
    });

    mockParams = { token: "valid-token" };
    render(<AcceptInvitationPage />);

    await waitFor(() => {
      expect(screen.getByText(/Únete a Acme Corp/i)).toBeInTheDocument();
    });

    fireEvent.change(screen.getByPlaceholderText(/Ej. Juan Pérez/i), {
      target: { value: "Nuevo Miembro" },
    });
    fireEvent.change(screen.getByPlaceholderText(/Mínimo 8 caracteres/i), {
      target: { value: "password123" },
    });
    fireEvent.change(screen.getByPlaceholderText(/Repite tu contraseña/i), {
      target: { value: "password123" },
    });

    fireEvent.click(screen.getByRole("button", { name: /Aceptar Invitación/i }));

    await waitFor(() => {
      expect(api.acceptInvitation).toHaveBeenCalledWith("valid-token", {
        full_name: "Nuevo Miembro",
        password: "password123",
      });
      expect(mockPush).toHaveBeenCalledWith("/");
    });
  });
});
