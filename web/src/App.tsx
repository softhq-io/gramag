import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom'
import { AuthProvider } from './auth/AuthContext'
import { ProtectedRoute } from './auth/ProtectedRoute'
import { AppLayout } from './layouts/AppLayout'
import { LoginPage } from './pages/LoginPage'
import { MissionBriefingPage } from './pages/MissionBriefingPage'
import { FleetDashboardPage } from './pages/FleetDashboardPage'
import { PartDetailPage } from './pages/PartDetailPage'
import { ProtoPage } from './pages/ProtoPage'
import { UsersAdminPage } from './pages/UsersAdminPage'
import { SuperadminRoute } from './auth/SuperadminRoute'
import './i18n'

function App() {
  return (
    <AuthProvider>
      <BrowserRouter basename="/einsatzplaner">
        <Routes>
          <Route path="/login" element={<LoginPage />} />
          <Route element={<ProtectedRoute><AppLayout /></ProtectedRoute>}>
            <Route index element={<Navigate to="/proto" replace />} />
            <Route path="/proto" element={<ProtoPage />} />
            <Route path="/proto/machine/:machineSlug" element={<ProtoPage />} />
            <Route path="/proto/machine/:machineSlug/chat/:chatId" element={<ProtoPage />} />
            <Route path="/mission/:machineErpId" element={<SuperadminRoute><MissionBriefingPage /></SuperadminRoute>} />
            <Route path="/part/:partNummer" element={<SuperadminRoute><PartDetailPage /></SuperadminRoute>} />
            <Route path="/fleet" element={<SuperadminRoute><FleetDashboardPage /></SuperadminRoute>} />
            <Route path="/admin/users" element={<SuperadminRoute><UsersAdminPage /></SuperadminRoute>} />
            <Route path="*" element={<Navigate to="/proto" replace />} />
          </Route>
        </Routes>
      </BrowserRouter>
    </AuthProvider>
  )
}

export default App
