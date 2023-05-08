import { Injectable } from '@angular/core';
import { HttpClient, HttpHeaders, HttpRequest, HttpEventType, HttpErrorResponse } from '@angular/common/http';
import { Observable, throwError } from 'rxjs';
import { catchError, retry } from 'rxjs/operators';
@Injectable({
  providedIn: 'root'
})
export class ApiServiceService {

  baseURL : string = "https://jrootham.ca/test-bids/api/"

  constructor(private $http:HttpClient) { }

  getCommodities(successCallback: (result: any) => void){
    const url = this.baseURL + 'commodities';

    let httpHeaders = new HttpHeaders()
    .set('Cache-Control', 'no-cache, no-store, must-revalidate')
    .set('Pragma', 'no-cache')

    const httpOptions =  {
			headers: httpHeaders,
			observe: 'events',
		};

    const req = new HttpRequest('GET', url, '', httpOptions);
		this.$http.request(req)
			.subscribe({
				next: event => {
					if (event.type === HttpEventType.Response) {
						if (successCallback) {
							successCallback(event.body);
						}
					}
				},
				error: rejection => {
          console.log(rejection);
				}
			});

  }
}
