import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom'
import { AuthProvider } from './auth/AuthContext'
import { ProtectedRoute } from './auth/ProtectedRoute'
import { AppLayout } from './layouts/AppLayout'
import { LoginPage } from './pages/LoginPage'
import { SearchPage } from './pages/SearchPage'
import { MissionBriefingPage } from './pages/MissionBriefingPage'
import { FleetDashboardPage } from './pages/FleetDashboardPage'
import { PartDetailPage } from './pages/PartDetailPage'
import { ProtoPage } from './pages/ProtoPage'
import { UsersAdminPage } from './pages/UsersAdminPage'
import { SuperadminRoute } from './auth/SuperadminRoute'
import './i18n'

const PROTO_ONLY = (import.meta.env.VITE_PROTO_ONLY as string) === '1'

function App() {
  return (
    <AuthProvider>
      <BrowserRouter basename="/einsatzplaner">
        <Routes>
          <Route path="/login" element={<LoginPage />} />
          <Route element={<ProtectedRoute><AppLayout /></ProtectedRoute>}>
            {PROTO_ONLY ? (
              <Route index element={<Navigate to="/proto" replace />} />
            ) : (
              <>
                <Route index element={<SearchPage />} />
                <Route path="/mission/:machineErpId" element={<MissionBriefingPage />} />
                <Route path="/part/:partNummer" element={<PartDetailPage />} />
                <Route path="/fleet" element={<FleetDashboardPage />} />
              </>
            )}
            <Route path="/proto" element={<ProtoPage />} />
            <Route path="/admin/users" element={<SuperadminRoute><UsersAdminPage /></SuperadminRoute>} />
          </Route>
        </Routes>
      </BrowserRouter>
    </AuthProvider>
  )
}

export default App
