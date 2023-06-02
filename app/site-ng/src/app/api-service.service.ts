import { Injectable } from '@angular/core';
import { HttpClient, HttpHeaders, HttpRequest, HttpEventType, HttpErrorResponse } from '@angular/common/http';
import { Observable, throwError } from 'rxjs';
import { catchError, retry } from 'rxjs/operators';
import { Commodity } from './models/models';
@Injectable({
  providedIn: 'root'
})
export class ApiServiceService {

  // baseURL : string = 'http://localhost:3305/toronto-bids/api/'
  baseURL: string = 'https://jrootham.ca/dev-bids/api/'

  constructor(private $http: HttpClient) { }

  getDivisions() {
    return getDivisions();
  }

  getOfferTypes() {
    return getOfferTypes();
  }

  getCommodities() {
    return getCommodities();
    //   const url = this.baseURL + 'commodities';

    //   let httpHeaders = new HttpHeaders()
    //   .set('Cache-Control', 'no-cache, no-store, must-revalidate')
    //   .set('Pragma', 'no-cache')

    // return this.$http.get<Commodity[]>(url);
  }

  getCommodityTypes() {
    return getCommodityTypes();
  }

  getSearchResults() {
    return getMockSearchResults();
  }
}

function getMockSearchResults() {
  return [

    {
      'division': '',
      'call_number': 'Doc3760842012',
      'document_id': 74,
      'type': 'Summary Notice and Notice of Intended Procurement',
      'commodity': 'Goods and Services',
      'short_description': 'Request for Quotations for the Non-Exclusive Supply and Delivery of Fine Paper and Related Paper Products for Various Ci',
      'closing_date': new Date('2023-01-31T00:00:00Z'),
      'buyers': [
        {
          'buyer': 'Mutinelli-Djukic, Alexandra',
          'phone': '416-397-5192',
          'email': 'Alexandra.mutinelli-djukic@toronto.ca',
          'location': 'City Hall, 100 Queen Street West, West Tower, 17th Floor, Toronto, ON M5H 2N2'
        }
      ],
      'commodity_type': 'Fine / Bond Paper',
      'posting_date': new Date('2022-01-03T00:00:00Z')
    },
    {
      'division': 'Engineering & Construction Services - Capital Works Delivery',
      'call_number': 'Doc3704154726',
      'document_id': 106,
      'type': 'Request for Proposal',
      'commodity': 'Professional Services',
      'short_description': 'Geotechnical and Hydrogeological Services for Preliminary Design of the Black Creek EA Solution and Detailed Design',
      'closing_date': new Date('2023-01-10T00:00:00Z'),
      'buyers': [
        {
          'buyer': 'Yang, Aimee',
          'phone': '416-397-4803',
          'email': 'Aimee.Yang@toronto.ca',
          'location': 'City Hall, 19th Floor West Tower'
        }
      ],
      'commodity_type': 'Environmental Services',
      'posting_date': new Date('2022-11-02T00:00:00Z')
    },
    {
      'division': 'Engineering & Construction Services - Capital Works Delivery',
      'call_number': 'Doc3635059417',
      'document_id': 100,
      'type': 'Tender',
      'commodity': 'Professional Services',
      'short_description': 'Standby Power System Optimization and Miscellaneous Electrical Upgrades at F.J. Horgan Water Treatment Plant',
      'closing_date': new Date('2023-01-18T00:00:00Z'),
      'buyers': [
        {
          'buyer': 'Yang, Aimee',
          'phone': '416-397-4803',
          'email': 'Aimee.Yang@toronto.ca',
          'location': 'City Hall, 19th Floor West Tower'
        }
      ],
      'commodity_type': 'Engineering Services',
      'posting_date': new Date('2022-11-04T00:00:00Z')
    },
    {
      'division': 'City Planning',
      'call_number': 'Doc3670282580',
      'document_id': 85,
      'type': 'Notice of Intended Procurement',
      'commodity': 'Professional Services',
      'short_description': 'Landscape Architect Consulting Services',
      'closing_date': new Date('2023-01-20T00:00:00Z'),
      'buyers': [
        {
          'buyer': 'Landrito, Donna',
          'phone': '416-392-7314',
          'email': 'Donna.Landrito@toronto.ca',
          'location': 'City Hall, 17th Floor West Tower'
        }
      ],
      'commodity_type': 'Consulting Services',
      'posting_date': new Date('2022-11-07T00:00:00Z')
    },
    {
      'division': 'Fleet Services',
      'call_number': 'Doc3705834628',
      'document_id': 81,
      'type': 'Request for Quotation',
      'commodity': 'Goods and Services',
      'short_description': 'Compressed Natural Gas (CNG) powered Natural Gas Vehicle (NGV)',
      'closing_date': new Date('2023-01-17T00:00:00Z'),
      'buyers': [
        {
          'buyer': 'Ghosh, Antora',
          'phone': '416-392-7468',
          'email': 'antora.ghosh@toronto.ca',
          'location': '100 Queen Street West 17th Floor'
        }
      ],
      'commodity_type': 'Preventative Maintenance and Services',
      'posting_date': new Date('2022-11-09T00:00:00Z')
    },
    {
      'division': '',
      'call_number': 'blank_63bce10586473',
      'document_id': 94,
      'type': 'Summary Notice and Notice of Intended Procurement',
      'commodity': 'Professional Services',
      'short_description': 'OE/TA Services for Delivery of the Gardiner Expressway Rehabilitation Project Section 3 – Highway 427 to Humber River',
      'closing_date': new Date('2023-04-04T00:00:00Z'),
      'buyers': [
        {
          'buyer': 'Hampton, Richard',
          'phone': '416-338-2544',
          'email': 'Richard.Hampton@toronto.ca',
          'location': 'City Hall, 18th Floor West Tower'
        }
      ],
      'commodity_type': 'Consulting Services',
      'posting_date': new Date('2022-11-15T00:00:00Z')
    },
    {
      'division': 'Engineering & Construction Services - Capital Works Delivery',
      'call_number': 'blank_63bce1058649b',
      'document_id': 97,
      'type': 'Summary Notice and Notice of Intended Procurement',
      'commodity': 'Professional Services',
      'short_description': 'OE/TA Services for Delivery of the Gardiner Expressway Rehabilitation Project Section 4 – Grand Magazine to York Street',
      'closing_date': new Date('2023-04-04T00:00:00Z'),
      'buyers': [
        {
          'buyer': 'Hampton, Richard',
          'phone': '416-338-2544',
          'email': 'Richard.Hampton@toronto.ca',
          'location': 'City Hall, 18th Floor West Tower'
        }
      ],
      'commodity_type': 'Consulting Services',
      'posting_date': new Date('2022-11-15T00:00:00Z')
    },
    {
      'division': '',
      'call_number': 'Doc3751442529',
      'document_id': 102,
      'type': 'Request for Proposal',
      'commodity': 'Professional Services',
      'short_description': 'Lawrence Park Neighbourhood Road Reconstruction and Basement Flooding Improvements',
      'closing_date': new Date('2023-01-20T00:00:00Z'),
      'buyers': [
        {
          'buyer': 'Kladianos, Tony',
          'phone': '416-338-5578',
          'email': 'Tony.Kladianos@toronto.ca',
          'location': 'City Hall, 17th Floor West Tower'
        }
      ],
      'commodity_type': 'Engineering Services',
      'posting_date': new Date('2022-11-18T00:00:00Z')
    },
    {
      'division': 'Engineering & Construction Services - Engineering Services',
      'call_number': 'Doc3740004804',
      'document_id': 101,
      'type': 'Request for Proposal',
      'commodity': 'Professional Services',
      'short_description': 'Professional Engineering Services Eastern/Adelaide Bridges (ID246, ID263, ID264 & ID266)',
      'closing_date': new Date('2023-01-10T00:00:00Z'),
      'buyers': [
        {
          'buyer': 'Kalanderopoulos, Amee',
          'phone': '416-392-5011',
          'email': 'amee.kalanderopoulos@toronto.ca',
          'location': '100 Queen Street West'
        }
      ],
      'commodity_type': 'Engineering Services',
      'posting_date': new Date('2022-11-21T00:00:00Z')
    },
    {
      'division': 'Engineering & Construction Services - Engineering Services',
      'call_number': 'Doc3754640010',
      'document_id': 60,
      'type': 'Notice of Intended Procurement',
      'commodity': 'Construction Services',
      'short_description': 'ASHBRIDGES BAY TREATMENT PLANT – D BUILDING PHASE 2 UPGRADES',
      'closing_date': new Date('2023-02-10T00:00:00Z'),
      'buyers': [
        {
          'buyer': 'Landrito, Donna',
          'phone': '416-392-7314',
          'email': 'Donna.Landrito@toronto.ca',
          'location': 'City Hall, 17th Floor West Tower'
        }
      ],
      'commodity_type': 'Construction Services',
      'posting_date': new Date('2022-11-23T00:00:00Z')
    }
  ];

}

function getDivisions() {
  return [
    {
      'id': 13,
      'division': ""
    },
    {
      'id': 22,
      'division': "City Manager's Office"
    },
    {
      'id': 20,
      'division': "City Planning"
    },
    {
      'id': 16,
      'division': "Corporate Real Estate Management"
    },
    {
      'id': 12,
      'division': "Engineering & Construction Services - Capital Works Delivery"
    },
    {
      'id': 15,
      'division': "Engineering & Construction Services - Engineering Services"
    },
    {
      'id': 18,
      'division': "Fire Services"
    },
    {
      'id': 19,
      'division': "Fleet Services"
    },
    {
      'id': 14,
      'division': "Parks, Forestry & Recreation"
    },
    {
      'id': 17,
      'division': "Toronto Water"
    },
    {
      'id': 21,
      'division': "Transportation Services"
    }
  ];

}

function getOfferTypes() {
  return [
    {
      'id': 14,
      'type': "Expression of Interest"
    },
    {
      'id': 9,
      'type': "Notice of Intended Procurement"
    },
    {
      'id': 10,
      'type': "Request for Proposal"
    },
    {
      'id': 11,
      'type': "Request for Quotation"
    },
    {
      'id': 12,
      'type': "Request for Quotation - Prequalification"
    },
    {
      'id': 13,
      'type': "Summary Notice and Notice of Intended Procurement"
    },
    {
      'id': 8,
      'type': "Tender"
    }
  ]
}

function getCommodities() {
  return [{ display: 'Construction Services', value: 4 }, { display: 'Goods and Services', value: 5 }, { display: 'Professional Services', value: 6 }];
}

function getCommodityTypes() {
  return [
    {
      'id': 7,
      'commodity_id': 1,
      'commodity_type': ""
    },
    {
      'id': 1,
      'commodity_id': 1,
      'commodity_type': "Bridge Repairs"
    },
    {
      'id': 2,
      'commodity_id': 1,
      'commodity_type': "Construction Services"
    },
    {
      'id': 3,
      'commodity_id': 1,
      'commodity_type': "Facilities renovations"
    },
    {
      'id': 4,
      'commodity_id': 1,
      'commodity_type': "Landscape Construction"
    },
    {
      'id': 5,
      'commodity_id': 1,
      'commodity_type': "Sewer repair and maintenance"
    },
    {
      'id': 6,
      'commodity_id': 1,
      'commodity_type': "Watermains - Water Services"
    },
    {
      'id': 8,
      'commodity_id': 2,
      'commodity_type': "Fine / Bond Paper"
    },
    {
      'id': 9,
      'commodity_id': 2,
      'commodity_type': "Hospital - Medical - Dental - Supplies / Equipment / Services"
    },
    {
      'id': 10,
      'commodity_id': 2,
      'commodity_type': "Information & Technology Software/Hardware"
    },
    {
      'id': 11,
      'commodity_id': 2,
      'commodity_type': "Overhead Doors - Supply / Repair / Maintenance / Parts"
    },
    {
      'id': 12,
      'commodity_id': 2,
      'commodity_type': "Preventative Maintenance and Services"
    },
    {
      'id': 13,
      'commodity_id': 2,
      'commodity_type': "Vehicle - Parts / Tires / Repair / Maintenance / Service"
    },
    {
      'id': 14,
      'commodity_id': 2,
      'commodity_type': "Waste Removal and Haulage"
    },
    {
      'id': 15,
      'commodity_id': 3,
      'commodity_type': "Architectural Services"
    },
    {
      'id': 16,
      'commodity_id': 3,
      'commodity_type': "Consulting Services"
    },
    {
      'id': 17,
      'commodity_id': 3,
      'commodity_type': "Engineering Services"
    },
    {
      'id': 18,
      'commodity_id': 3,
      'commodity_type': "Environmental Services"
    },
    {
      'id': 19,
      'commodity_id': 3,
      'commodity_type': "Testing & Inspection Services"
    }
  ];
}
