import { Injectable } from '@angular/core';
import { HttpClient, HttpHeaders, HttpRequest, HttpEventType, HttpErrorResponse } from '@angular/common/http';
import { Observable, throwError } from 'rxjs';
import { catchError, retry } from 'rxjs/operators';
import { Commodity } from './search-component/search-component.component';
@Injectable({
  providedIn: 'root'
})
export class ApiServiceService {

  baseURL : string = "http://localhost:3305/toronto-bids/api/"

  constructor(private $http:HttpClient) { }

  getCommodities() : Observable<Commodity[]> {
    const url = this.baseURL + 'commodities';

    let httpHeaders = new HttpHeaders()
    .set('Cache-Control', 'no-cache, no-store, must-revalidate')
    .set('Pragma', 'no-cache')

	return this.$http.get<Commodity[]>(url);
  }
}
