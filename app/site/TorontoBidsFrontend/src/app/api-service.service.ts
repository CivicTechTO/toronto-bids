import { Injectable } from '@angular/core';
import { HttpClient, HttpHeaders, HttpRequest, HttpEventType, HttpErrorResponse } from '@angular/common/http';
import { Observable, throwError } from 'rxjs';
import { catchError, retry } from 'rxjs/operators';
import { Commodity } from './search-component/search-component.component';
@Injectable({
  providedIn: 'root'
})
export class ApiServiceService {

  // baseURL : string = 'http://localhost:3305/toronto-bids/api/'
  baseURL : string = 'https://jrootham.ca/dev-bids/api/'

  constructor(private $http:HttpClient) { }

  getCommodities() : Observable<Commodity[]> {
    const url = this.baseURL + 'commodities';

    let httpHeaders = new HttpHeaders()
    .set('Cache-Control', 'no-cache, no-store, must-revalidate')
    .set('Pragma', 'no-cache')

	return this.$http.get<Commodity[]>(url);
  }

  getSearchResults(){
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
      'closing_date': new Date ('2023-01-31T00:00:00Z'),
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