import { makeLead, type Lead } from '@/domain/types'

// Ported verbatim from 03-my-leads.html (LEADS array) so parity is verifiable.
export const MOCK_LEADS: Lead[] = [
  makeLead({ name: 'Priya Patel', stage: 'new', phone: '+1 647-555-0707', email: 'priya.patel@gmail.com', licenceYear: '2021 or later', received: 'Today 9:12am' }),
  makeLead({ name: 'Marcus Lee', stage: 'new', phone: '+1 416-555-0142', email: 'marcus.lee@gmail.com', licenceYear: '2018-2020', received: 'Today 8:40am' }),
  makeLead({ name: 'Aisha Khan', stage: 'new', phone: '+1 905-555-0188', email: 'aisha.khan@gmail.com', licenceYear: '2021 or later', received: 'Today 8:05am', campaign: 'Campaign B' }),
  makeLead({ name: 'Tom Becker', stage: 'new', phone: '+1 289-555-0119', email: 'tom.becker@gmail.com', licenceYear: '2015-2017', received: 'Yesterday 6:30pm' }),

  makeLead({ name: 'Sofia Rossi', stage: 'called', phone: '+1 647-555-0231', email: 'sofia.rossi@gmail.com', licenceYear: '2018-2020', attempts: 1, lastContact: 'Today 10:15am' }),
  makeLead({ name: 'David Chen', stage: 'called', phone: '+1 416-555-0455', email: 'david.chen@gmail.com', licenceYear: '2021 or later', attempts: 2, lastContact: 'Yesterday 4:12pm' }),

  makeLead({ name: 'Nina Williams', stage: 'contacted', phone: '+1 905-555-0322', email: 'nina.w@gmail.com', licenceYear: '2015-2017', attempts: 1, lastContact: 'Today 11:02am' }),

  makeLead({ name: 'Raj Mehta', stage: 'followup', phone: '+1 647-555-0610', email: 'raj.mehta@gmail.com', licenceYear: '2018-2020', attempts: 2, lastContact: 'Today 9:50am', dueToday: true, currentPremium: '$2,140 / yr', insurer: 'Intact' }),
  makeLead({ name: 'Grace Okafor', stage: 'followup', phone: '+1 416-555-0733', email: 'grace.o@gmail.com', licenceYear: '2021 or later', attempts: 1, lastContact: 'Yesterday 2:00pm', dueToday: true }),
  makeLead({ name: 'Leo Martin', stage: 'followup', phone: '+1 289-555-0844', email: 'leo.martin@gmail.com', licenceYear: '2015-2017', attempts: 3, lastContact: '2 days ago' }),

  makeLead({
    name: 'Hannah Park', stage: 'quoted', phone: '+1 647-555-0911', email: 'hannah.park@gmail.com', licenceYear: '2021 or later', licenceNumber: 'P1234-56789-01234', attempts: 2, lastContact: 'Today 8:55am', currentPremium: '$2,480 / yr', myPremium: '$1,990 / yr', insurer: 'Aviva', effectiveDate: '2026-07-01',
    // mature lead — capture form filled in
    firstName: 'Hannah', lastName: 'Park', dob: '1992-04-18', street: '42 Maple Ave', city: 'Toronto', postal: 'M4B 1B3',
    vehicles: [
      { vin: '2HGFC2F59NH500123', ymm: '2022 Honda Civic', condition: 'used', ownership: 'financed', financingCo: 'Honda Finance', purchaseDate: '2022-09-15', oneWayKm: '18', annualKm: '16000', winterTires: 'yes', coverageType: 'full', antiTheft: 'yes', parkingNight: 'driveway', useType: 'commute' },
    ],
    gDate: '2014-06-01', g2Date: '2012-03-01', g1Date: '2011-01-01', bundle: 'tenant',
  }),
  makeLead({ name: 'Omar Farouk', stage: 'quoted', phone: '+1 905-555-0177', email: 'omar.f@gmail.com', licenceYear: '2018-2020', attempts: 2, lastContact: 'Yesterday 5:20pm', currentPremium: '$3,010 / yr', myPremium: '$2,420 / yr', insurer: 'Wawanesa' }),

  makeLead({ name: 'Emma Schmidt', stage: 'awaiting', phone: '+1 416-555-0266', email: 'emma.s@gmail.com', licenceYear: '2021 or later', attempts: 3, lastContact: 'Today 7:40am', myPremium: '$1,760 / yr', insurer: 'Travelers', effectiveDate: '2026-06-25' }),

  makeLead({ name: 'Carlos Diaz', stage: 'sold', phone: '+1 647-555-0388', email: 'carlos.d@gmail.com', licenceYear: '2018-2020', attempts: 3, lastContact: 'Yesterday 1:15pm', myPremium: '$2,050 / yr', insurer: 'Intact', effectiveDate: '2026-07-05' }),
  makeLead({ name: 'Yuki Tanaka', stage: 'submitted', phone: '+1 905-555-0499', email: 'yuki.t@gmail.com', licenceYear: '2021 or later', attempts: 4, lastContact: '2 days ago', myPremium: '$1,880 / yr', insurer: 'Economical', effectiveDate: '2026-07-10' }),
  makeLead({ name: 'Fatima Noor', stage: 'issued', phone: '+1 416-555-0512', email: 'fatima.n@gmail.com', licenceYear: '2018-2020', attempts: 4, lastContact: '3 days ago', myPremium: '$2,210 / yr', insurer: 'RSA', effectiveDate: '2026-06-15' }),

  makeLead({ name: 'Greg Thomas', stage: 'notint', phone: '+1 647-555-0623', email: 'greg.t@gmail.com', licenceYear: '2015-2017', attempts: 2, lastContact: 'Yesterday 3:30pm' }),
  makeLead({ name: 'Unknown Caller', stage: 'invalid', phone: '+1 000-000-0000', email: '', licenceYear: '', attempts: 1, lastContact: 'Today 8:00am' }),
]
