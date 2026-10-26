import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom'
import { Toaster } from '@/components/ui/sonner'
import { ConfirmProvider } from '@/components/ConfirmDialog'
import { AuthProvider } from '@/auth/AuthContext'
import { RequireAuth } from '@/auth/RequireAuth'
import Login from '@/routes/Login'
import MyDay from '@/routes/MyDay'
import MyLeadCost from '@/routes/MyLeadCost'
import MvrLog from '@/routes/mvr/MvrLog'
import MvrReport from '@/routes/mvr/MvrReport'
import Leads from '@/routes/Leads'
import Reminders from '@/routes/Reminders'
import BrokerGroupList from '@/routes/admin/brokerGroups/BrokerGroupList'
import BrokerGroupDetail from '@/routes/admin/brokerGroups/BrokerGroupDetail'
import BrokerDetail from '@/routes/admin/brokerGroups/BrokerDetail'
import OnboardBroker from '@/routes/admin/onboardBroker/OnboardBroker'
import AdminHub from '@/routes/admin/AdminHub'
import CommissionRates from '@/routes/admin/commission/CommissionRates'
import LeadCost from '@/routes/admin/leadCost/LeadCost'
import AdminLeadList from '@/routes/admin/leads/AdminLeadList'
import CapiDashboard from '@/routes/admin/capi/CapiDashboard'
import CapiLeads from '@/routes/admin/capi/CapiLeads'
import WebsiteLeads from '@/routes/admin/websiteLeads/WebsiteLeads'
import MobileLeads from '@/routes/mobile/MobileLeads'
import MobileMyDay from '@/routes/mobile/MobileMyDay'
import { PhoneRedirect } from '@/routes/mobile/PhoneRedirect'
import { homeRoute } from '@/routes/mobile/isPhone'

export default function App() {
  return (
    <AuthProvider>
      <ConfirmProvider>
        <BrowserRouter>
          {/* Sends a phone to /m on arrival. Inside the router because it
              uses its hooks; outside <Routes> because it must run whichever
              route matched. */}
          <PhoneRedirect />
          <Routes>
            {/* The root sends a phone to /m and everything else to the desktop.
                Hardcoding /my-day here beat PhoneRedirect to it: this
                <Navigate> runs during the same render, so a broker opening
                a bookmarked root URL on their phone landed on the desktop
                screen. Signing in was always correct — only this path was
                not. */}
            <Route path="/" element={<Navigate to={homeRoute()} replace />} />
            <Route path="/login" element={<Login />} />
            <Route path="/my-day" element={<RequireAuth><MyDay /></RequireAuth>} />
            <Route path="/my-lead-cost" element={<RequireAuth><MyLeadCost /></RequireAuth>} />
            <Route path="/leads" element={<RequireAuth><Leads /></RequireAuth>} />
            <Route path="/reminders" element={<RequireAuth><Reminders /></RequireAuth>} />
            <Route path="/mvr-log" element={<RequireAuth><MvrLog /></RequireAuth>} />
            {/* PUBLIC: the head-office MVR report, opened with its own
                password rather than a CRM login. No RequireAuth by design. */}
            <Route path="/mvr-report" element={<MvrReport />} />
            {/* THE MOBILE COMPANION — its own screens, not responsive
                variants of the desktop ones. Deliberately outside the desktop
                AppShell: a phone has no room for a top bar of nav it will
                never use, and the two are different design languages.

                /m          My Day — the landing screen
                /m/leads    the list, filtered by ?stage=
                            (the detail is a pushed view inside it, not yet
                            its own URL — see the note in MobileLeads) */}
            <Route path="/m" element={<RequireAuth><MobileMyDay /></RequireAuth>} />
            <Route path="/m/leads" element={<RequireAuth><MobileLeads /></RequireAuth>} />
            <Route path="/admin" element={<RequireAuth><AdminHub /></RequireAuth>} />
            <Route path="/admin/commission" element={<RequireAuth><CommissionRates /></RequireAuth>} />
            <Route path="/admin/lead-cost" element={<RequireAuth><LeadCost /></RequireAuth>} />
            <Route path="/admin/leads" element={<RequireAuth><AdminLeadList /></RequireAuth>} />
            <Route path="/admin/capi" element={<RequireAuth><CapiLeads /></RequireAuth>} />
            <Route path="/admin/capi/details" element={<RequireAuth><CapiDashboard /></RequireAuth>} />
            <Route path="/admin/website-leads" element={<RequireAuth><WebsiteLeads /></RequireAuth>} />
            <Route path="/admin/broker-groups" element={<RequireAuth><BrokerGroupList /></RequireAuth>} />
            <Route path="/admin/broker-groups/:groupId" element={<RequireAuth><BrokerGroupDetail /></RequireAuth>} />
            <Route
              path="/admin/broker-groups/:groupId/brokers/:brokerId"
              element={<RequireAuth><BrokerDetail /></RequireAuth>}
            />
            <Route path="/admin/onboard-broker" element={<RequireAuth><OnboardBroker /></RequireAuth>} />
            {/* homeRoute(), not a hardcoded /my-day — for the same reason the
                root route above uses it. A stale bookmark or an old link on a
                phone lands here, and this <Navigate> runs during the render
                that PhoneRedirect's effect comes after, so hardcoding the
                desktop path won the race and stranded the broker there. */}
            <Route path="*" element={<Navigate to={homeRoute()} replace />} />
          </Routes>
        </BrowserRouter>
        <Toaster richColors position="bottom-center" />
      </ConfirmProvider>
    </AuthProvider>
  )
}
